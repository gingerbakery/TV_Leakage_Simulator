"""Real CUDA BSDF acceptance: CPU parity, independent integral, repeat timing.

Prepare the source checkout with run_web_gpu.ps1 -PreflightOnly first.
This program fails on missing CUDA or any transport fallback; no simulated GPU.
No LightTools results are synthesized or inferred from these tests.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts'),str(ROOT/'tools')]
from verify_gpu_cuda_runtime import verify_device
from perf4_accuracy import compare_semantic_payloads
from benchmark_perf4b_resident_wavefront import _semantic_payload
from validate_bsdf_transport import make_asset, scene_input, oracle, scores, write_map
from leakage_simulator.bsdf_import import import_lt_ascii_bsdf
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import OpticalProfile
from leakage_simulator.types import EmitterSpec


def evidence(result):
    p=result.metrics['_performance_summary']
    fields=('compute_execution_state','compute_execution_reason','gpu_cuda_device_name',
        'gpu_cuda_gpu_attempt_count','gpu_cuda_gpu_success_count',
        'gpu_cuda_hybrid_cpu_success_count','intersection_fallback_count',
        'gpu_resident_wavefront_success_count','gpu_resident_wavefront_fallback_count',
        'gpu_resident_wavefront_fallback_reason','gpu_summary_accumulator_success_count',
        'wavefront_planner_fallback_count','wavefront_reducer_fallback_count',
        'gpu_resident_wavefront_kernel_sec','gpu_resident_wavefront_jit_compile_sec',
        'gpu_summary_accumulator_kernel_sec','wavefront_residency','monte_carlo_contract',
        'bsdf_transport')
    return {k:p.get(k) for k in fields}


def real_transport_pass(e):
    return (e['compute_execution_state']=='gpu_active' and e['gpu_cuda_gpu_success_count']>0
        and e['gpu_resident_wavefront_success_count']>0
        and all(e[k]==0 for k in ('gpu_cuda_hybrid_cpu_success_count','intersection_fallback_count',
            'gpu_resident_wavefront_fallback_count','wavefront_planner_fallback_count','wavefront_reducer_fallback_count')))


def physical_float_parity(reference,candidate):
    """Exact discrete data; FP64 absolute/relative test including near-zero geometry.

    The existing ULP audit is retained separately. Subtracting two coordinates
    near 1 mm can leave ~1e-16 mm with enormous ULP distance near zero, despite
    a physically negligible absolute error. Never relax bin/count decisions.
    """
    if isinstance(reference,dict) and isinstance(candidate,dict):
        return reference.keys()==candidate.keys() and all(physical_float_parity(v,candidate[k]) for k,v in reference.items())
    if isinstance(reference,(list,tuple)) and isinstance(candidate,(list,tuple)):
        return len(reference)==len(candidate) and all(physical_float_parity(a,b) for a,b in zip(reference,candidate))
    if type(reference) is float and type(candidate) is float:
        return math.isfinite(reference) and math.isfinite(candidate) and math.isclose(reference,candidate,rel_tol=1e-12,abs_tol=1e-12)
    return type(reference) is type(candidate) and reference==candidate


def run_pair(trace,out,name,repeats=3,**overrides):
    trace.config.compute_backend='cpu'
    started=time.perf_counter();cpu=run_direct_ray_trace(trace);cpu_sec=time.perf_counter()-started
    trace.config.compute_backend='gpu_cuda'
    runs=[]
    for i in range(repeats):
        started=time.perf_counter();gpu=run_direct_ray_trace(trace,**overrides);elapsed=time.perf_counter()-started
        a,b=_semantic_payload(cpu),_semantic_payload(gpu)
        parity=compare_semantic_payloads(a,b).to_dict()
        # ULP audit is diagnostic; acceptance follows the dimensioned FP64
        # criterion stated in the report, including values close to zero.
        physical_pass=physical_float_parity(a,b)
        proof=evidence(gpu)
        optical=gpu.metrics['_optical_summary']['profile_hits'].get('measured')
        metadata_pass=not optical or optical['scatter_model']=='bsdf'
        runs.append({'wall_sec':elapsed,'strict_ulp_audit':parity,
            'float64_absolute_relative_parity':physical_pass,'evidence':proof,
            'pass':bool(parity['discrete_exact'] and physical_pass and metadata_pass and real_transport_pass(proof))})
    row={'case':name,'rays':trace.emitters[0].ray_count,'emitter_types':[e.emitter_type for e in trace.emitters],
        'cpu_wall_sec':cpu_sec,'gpu_first_sec':runs[0]['wall_sec'],
        'gpu_warm_sec':[r['wall_sec'] for r in runs[1:]],'runs':runs,
        'pass':all(r['pass'] for r in runs)}
    for backend,result in (('cpu',cpu),('gpu',gpu)):
        (out/f'{name}-{backend}-result.json').write_text(json.dumps(result.to_dict()),encoding='utf-8')
    print(json.dumps({k:v for k,v in row.items() if k!='runs'}),flush=True)
    return row,cpu,gpu


def mutate_rt(asset,values):
    files=[dict(f) for f in asset['files']]
    rt=next(f for f in files if f['name']=='rt.txt')
    rt['text']='DFAT Version 1.0\nABSORBING: YES\nINDEX: 1\nDATAITEMS: TAVG RAVG\nwv 550\n'+ '\n'.join(f'AOI {a} 0 {r}' for a,r in values)
    fresh=import_lt_ascii_bsdf({'files':files});fresh['transport']=asset['transport']
    return fresh


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rays',type=int,default=100000)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/bsdf-gpu-validation')
    args=parser.parse_args()
    if args.rays<100000:parser.error('Acceptance requires at least 100000 primary rays')
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    preflight=verify_device()
    report={'scope':'Real CUDA vs CPU and independent numerical integral; NOT LT validation',
        'lt_status':'not_run','delivery':'source checkout','preflight':preflight,
        'utc':datetime.now(timezone.utc).isoformat(),
        'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'local_changes':True,'criteria':{'correlation_min':.99,'overlap_percent_min':95,
            'flux_error_percent_max':1,'gpu_transport_fallbacks':0},'cases':[],'edge_cases':[]}
    assets={k:make_asset(out/k,k=='directional') for k in ('lambertian','directional')}
    plots=[]
    for kind,angle in [('lambertian',0),('directional',0),('directional',30),('directional',60),('directional',80),('directional',45)]:
        name=f'{kind}-aoi{angle}';asset,interpolators=assets[kind]
        trace=scene_input(asset,angle,args.rays,7300+angle)
        row,cpu,gpu=run_pair(trace,out,name)
        actual=np.array(gpu.receiver_grids[0].flux_lumen)
        expected=oracle(interpolators,angle,2)
        coarse=oracle(interpolators,angle)
        delta=float(np.abs(expected-coarse).sum()/expected.sum()*100)
        measured=scores(actual,expected)
        row['oracle']={**measured,'quadrature_L1_delta_percent':delta,
            'received_lm':float(actual.sum()),'oracle_received_lm':float(expected.sum())}
        emitted=gpu.metrics['_reflection_summary']['lobes']['bsdf']['emitted_flux_lumen']
        row['emitted_lm']=emitted
        row['pass'] &= bool(abs(measured['flux_error_percent'])<=1 and measured['correlation']>=.99
            and measured['distribution_overlap_percent']>=95 and delta<.02 and abs(emitted-.3)<1e-10)
        write_map(out/f'{name}-gpu.csv',actual);write_map(out/f'{name}-oracle.csv',expected)
        request={'emitters':[asdict(e) for e in trace.emitters],'receivers':[asdict(r) for r in trace.receivers],
            'config':asdict(trace.config),'optical_profiles':[p.to_dict() for p in trace.optical_profiles],
            'bsdf_assets':trace.bsdf_assets}
        (out/f'{name}-request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
        report['cases'].append(row);plots.append((name,actual,expected))
        print(name,'oracle',json.dumps(row['oracle']),flush=True)

    # Actual end-to-end coverage beyond one reflected ray / one wavelength.
    asset=assets['directional'][0]
    for reflectance in (0.,1.):
        changed=mutate_rt(asset,[(0,reflectance),(90,reflectance)])
        trace=scene_input(changed,80,20000,811)
        row,_,_=run_pair(trace,out,f'rt-{reflectance:g}',1)
        report['edge_cases'].append(row)
    trace=scene_input(asset,45,20000,812)
    trace.config.max_depth=5;trace.config.store_ray_paths=True;trace.config.max_stored_paths=40
    trace.config.termination_mode='russian_roulette';trace.config.min_energy=.0005
    # Large ceiling and mixed material assignment exercise repeated BSDF and analytic events.
    trace.optical_profiles.append(OpticalProfile(profile_id='mirror',reflectance=.8,scatter_model='specular'))
    v=[trace.mesh.add_vertex(p) for p in [(-100,-100,1.5),(100,-100,1.5),(100,100,1.5),(-100,100,1.5)]]
    trace.mesh.add_face(v[2],v[1],v[0],'mirror');trace.mesh.add_face(v[3],v[2],v[0],'measured')
    row,_,_=run_pair(trace,out,'mixed-multibounce-roulette',3);report['edge_cases'].append(row)
    trace.config.contribution_mode='detailed'
    row,_,_=run_pair(trace,out,'mixed-multibounce-detailed',1);report['edge_cases'].append(row)
    trace.config.contribution_mode='summary'
    second=mutate_rt(asset,[(0,.1),(90,.8)])
    trace.bsdf_assets.append(second)
    trace.optical_profiles[1]=OpticalProfile(profile_id='mirror',reflectance=0,scatter_model='none',bsdf_asset_id=second['id'])
    row,_,_=run_pair(trace,out,'two-bsdf-materials',1);report['edge_cases'].append(row)
    trace=scene_input(asset,0,20000,814)
    # A physical source face at z=1 emits down to the measured reflector.
    v=[trace.mesh.add_vertex(p) for p in [(-.1,-.1,1),(.1,-.1,1),(.1,.1,1),(-.1,.1,1)]]
    trace.mesh.add_face(v[2],v[1],v[0],'source');trace.mesh.add_face(v[3],v[2],v[0],'source')
    trace.emitters=[EmitterSpec(emitter_id='face-beam',emitter_type='face',face_indices=[2,3],
        direction_distribution='gaussian',gaussian_sigma_deg=5,ray_count=20000,seed=814,power_lumen=1)]
    row,_,_=run_pair(trace,out,'face-emitter',1);report['edge_cases'].append(row)
    # Two measured wavelengths and distinct angular RT curves.
    files=[dict(f) for f in asset['files']]
    master=next(f for f in files if f['name']=='master.txt')
    master['text']+='\n'+master['text'].split('\n',1)[1].replace('Lambda = 550','Lambda = 650')
    rt=next(f for f in files if f['name']=='rt.txt')
    rt['text']+='\nwv 650\nAOI 0 0 0.6\nAOI 90 0 0.9\n'
    changed=import_lt_ascii_bsdf({'files':files})
    changed['transport']={**asset['transport'],'wavelengthNm':600}
    trace=scene_input(changed,45,20000,813)
    row,_,_=run_pair(trace,out,'wavelength-aoi-interpolation',1);report['edge_cases'].append(row)
    # Change GPU chunk size with the same primary RNG keys.
    row,_,_=run_pair(trace,out,'chunk-invariance',1,intersection_dispatch='batch',
        intersection_batch_size=3072,intersection_provider='gpu_cuda',wavefront_planner='numba_cpu',
        wavefront_pipeline='soa_event_tape',wavefront_reducer='numba_cpu',wavefront_rng='counter_rng_v2',
        wavefront_reducer_commit='run_accumulator',wavefront_residency='gpu_resident')
    report['edge_cases'].append(row)
    report['passed']=all(r['pass'] for r in report['cases']+report['edge_cases'])
    paths=list((ROOT/'src/leakage_simulator').glob('bsdf*.py'))+[ROOT/'src/leakage_simulator'/f for f in (
        'raytracer.py','native_cpu_counter_wavefront.py','gpu_cuda_resident_wavefront.py','native_cpu_ordered_reducer.py')]
    report['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,3,figsize=(12,7),layout='constrained')
    for ax,(name,actual,expected) in zip(axes.flat,plots):
        ax.imshow(actual,origin='lower',extent=(-10,10,-10,10),cmap='inferno')
        ax.set_title(f'{name}\nr = {scores(actual,expected)["correlation"]:.6f}')
        ax.set_xlabel('U (mm)');ax.set_ylabel('V (mm)')
    fig.savefig(out/'gpu-heatmaps.png',dpi=140);plt.close(fig)
    rows=''.join(f'<tr><td>{r["case"]}</td><td>{r["oracle"]["flux_error_percent"]:+.4f}%</td>'
        f'<td>{r["oracle"]["correlation"]:.6f}</td><td>{r["oracle"]["distribution_overlap_percent"]:.2f}%</td>'
        f'<td>{"PASS" if r["pass"] else "FAIL"}</td></tr>' for r in report['cases'])
    edge=''.join(f'<li>{r["case"]}: {"PASS" if r["pass"] else "FAIL"}</li>' for r in report['edge_cases'])
    first=report['cases'][0];timing=report['cases'][3]
    page=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>BSDF GPU 검증</title>
    <style>body{{max-width:1050px;margin:40px auto;padding:0 24px;font:17px/1.7 system-ui;color:#173149;background:#f8fafc}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #ccd}}img{{width:100%}}.note{{background:#fff0cb;padding:18px;border-radius:10px}}</style>
    <h1>BSDF GPU 입력·계산 검증 — {'PASS' if report['passed'] else 'FAIL'}</h1>
    <p>NVIDIA GeForce RTX 3070 · CUDA 13.1 · strict FP64 · 소스 작업본.</p>
    <p>LT ASCII master / Polar 산란 표 / DFAT RT를 입력해 AOI·파장 보간, 반사량, 산란 방향 추출을 CUDA에서 실행했다. CPU와 동일 seed로 광선 판정과 결과를 비교했다. 준비·파일 파싱은 CPU에서 수행한다.</p>
    <p class="note">아래 상관계수는 <strong>독립 수치 적분과 GPU 결과의 비교</strong>다. 실제 LightTools 교차 검증은 아직 하지 않았다. 지원 범위는 반사 전용 · 등방성 · No Shift · Source sampling이다.</p>
    <h2>6개 조건, 각 {args.rays:,} rays</h2><p>CPU/GPU 이산 판정은 정확히 같아야 한다. 실수는 절대 10⁻¹² 또는 상대 10⁻¹² 이내로 비교한다. 8 ULP 추가 감사도 JSON에 별도로 보존한다. 0 부근의 좌표 차이와 누적 합은 ULP 기준을 넘을 수 있어 실제 단위의 오차로 판정한다. 독립 적분 기준: 수광량 ≤1%, 상관 ≥0.99, 분포 겹침 ≥95%.</p>
    <table><tr><th>조건</th><th>수광량 편차</th><th>상관계수</th><th>분포 겹침</th><th>판정</th></tr>{rows}</table>
    <h2>추가 검증</h2><ul>{edge}</ul><p>GPU resident 성공 횟수와 모든 CPU fallback 수를 확인했다. 교차 판정만 GPU인 경우는 통과시키지 않는다. 상세 결과는 <a href="report.json">report.json</a>에 포함했다.</p>
    <h2>실행 시간</h2><p>같은 세션에서 첫 실행과 warm 2회를 기록했다. 첫 CUDA 조건: {first['gpu_first_sec']:.3f} / {' / '.join(f'{t:.3f}' for t in first['gpu_warm_sec'])}초. 방향성 60°: {timing['gpu_first_sec']:.3f} / {' / '.join(f'{t:.3f}' for t in timing['gpu_warm_sec'])}초. 동일 60° CPU 실행: {timing['cpu_wall_sec']:.3f}초. 이 작은 단일면 시험은 전체 조립체의 속도 보증이 아니다.</p>
    <img src="gpu-heatmaps.png" alt="6개 조건의 GPU 수광 분포"><p>기반 커밋 {report['base_commit'][:8]} + 미커밋 작업본. 모든 검증 소스의 SHA-256을 report.json에 기록했다.</p></html>'''
    (out/'report.html').write_text(page,encoding='utf-8')
    print('ACCEPTANCE',report['passed'],flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
