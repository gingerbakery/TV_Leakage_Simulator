"""CPU BSDF end-to-end benchmark and an honest, optional LT CSV comparison.

Run from the repository: python tools/validate_bsdf_transport.py --rays 100000
The oracle uses scipy interpolation + independent deterministic quadrature;
it does not use the production BSDF table, CDF, or normalization functions.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import csv
import html
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.interpolate import RegularGridInterpolator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leakage_simulator.bsdf_import import import_lt_ascii_bsdf
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterAimSpec, EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec

AOIS = (0, 30, 60, 80)
GRID = 20
WIDTH, Z, R = 20., 2., .3


def make_asset(folder: Path, directional: bool):
    folder.mkdir(parents=True, exist_ok=True)
    theta, phi = np.linspace(0, 90, 37), np.linspace(0, 360, 73)
    tt, pp = np.meshgrid(np.radians(theta), np.radians(phi), indexing='ij')
    master, files, interpolators = ['#UDOP AngGridMultiple 1.0'], [], {}
    for angle in AOIS:
        a = math.radians(angle)
        dot = np.cos(tt)*math.cos(a) - np.sin(tt)*np.sin(pp)*math.sin(a)
        values = .02 + np.exp(25*(dot-1)) if directional else np.ones_like(dot)
        # Serialize before oracle interpolation to use exactly the supplied samples.
        body = 'SphereMesh: 73 37 0 0 360 90\n' + '\n'.join(' '.join(f'{x:.12g}' for x in row) for row in values[::-1]) + '\n'
        name = f'scatter-{angle}.txt'
        files.append({'name': name, 'text': body})
        parsed = np.loadtxt(body.splitlines()[1:])[::-1]
        interpolators[angle] = RegularGridInterpolator((np.radians(theta), np.radians(phi)), parsed)
        master += [f'FileName = ..\\{name}', 'MeshFormat = Polar', 'MeshDataType = BSDF', f'RAOI = {angle}', 'Lambda = 550', 'POI = 0', 'Index = 1', 'IndexIncident = 1']
    files += [{'name': 'master.txt', 'text': '\n'.join(master)+'\n'},
              {'name': 'rt.txt', 'text': 'DFAT Version 1.0\nABSORBING: YES\nINDEX: 1\nDATAITEMS: TAVG RAVG\nwv 550\n' + '\n'.join(f'AOI {a} 0 {R}' for a in (*AOIS,90))+'\n'}]
    for f in files:
        (folder/f['name']).write_text(f['text'], encoding='utf-8')
    asset = import_lt_ascii_bsdf({'files': files})
    asset['transport'] = {'model': 'isotropic_no_shift', 'wavelengthNm': 550, 'aoiExtrapolation': 'clamp'}
    return asset, interpolators


def oracle(interpolators, angle, subdivisions=1):
    # Composite midpoint hemisphere quadrature, independent of the CDF cell integrator.
    nt, np_ = 720*subdivisions, 1440*subdivisions
    theta = (np.arange(nt)+.5)*(math.pi/2/nt)
    phi = (np.arange(np_)+.5)*(2*math.pi/np_)
    tt, pp = np.meshgrid(theta, phi, indexing='ij')
    angles = sorted(interpolators)
    if angle <= angles[0]: mixture = [(angles[0],1.)]
    elif angle >= angles[-1]: mixture = [(angles[-1],1.)]
    else:
        hi = next(a for a in angles if a >= angle); lo = angles[angles.index(hi)-1]
        fraction = (angle-lo)/(hi-lo)
        mixture = [(lo,1-fraction),(hi,fraction)]
    norms = {}
    for a,w in mixture:
        norms[a] = float(np.sum(interpolators[a]((tt,pp))*np.sin(tt)*np.cos(tt))*(math.pi/2/nt)*(2*math.pi/np_))
    # Receiver local U=X, V=-Y. Integrate projected BRDF over each square cell.
    # dOmega/dA = z/r^3 and cos(theta)=z/r => weight z^2/r^4.
    nodes, weights = np.polynomial.legendre.leggauss(16*subdivisions)
    edges = np.linspace(-WIDTH/2, WIDTH/2, GRID+1)
    coords = (edges[:-1,None]+edges[1:,None])/2 + (WIDTH/GRID/2)*nodes
    u, v = np.meshgrid(coords.ravel(), coords.ravel())
    radius = np.sqrt(u*u+v*v+Z*Z)
    th = np.arccos(Z/radius)
    ph = np.mod(np.arctan2(-u,-v),2*math.pi)
    value = sum(w*interpolators[a]((th,ph))/norms[a] for a,w in mixture)
    density = R*value*Z*Z/radius**4
    density = density.reshape(GRID,len(nodes),GRID,len(nodes))
    flux = np.einsum('iajb,a,b->ij',density,weights,weights)*(WIDTH/GRID/2)**2
    return flux


def scene_input(asset, angle, rays, seed):
    mesh=TriangleMesh()
    v=[mesh.add_vertex(p) for p in [(-100,-100,0),(100,-100,0),(100,100,0),(-100,100,0)]]
    mesh.add_face(v[0],v[1],v[2],'measured'); mesh.add_face(v[0],v[2],v[3],'measured')
    a=math.radians(angle)
    emitter=EmitterSpec(emitter_id='beam',emitter_type='datum_plane',center=(-math.tan(a),0,1),
        u_axis=(1,0,0),v_axis=(0,-1,0),width_mm=.000001,height_mm=.000001,
        power_lumen=1,ray_count=rays,seed=seed,emission_direction='forward',
        aim=EmitterAimSpec(enabled=True,mode='sphere',distribution='uniform_solid_angle',sphere_upper_deg=0,sphere_lower_deg=0,sphere_beta_deg=180-angle))
    receiver=ReceiverSpec(receiver_id='screen',center=(0,0,Z),normal=(0,0,-1),u_axis=(1,0,0),v_axis=(0,-1,0),
        width_mm=WIDTH,height_mm=WIDTH,resolution=(GRID,GRID),acceptance_angle_deg=90)
    return DirectRayTraceInput(mesh=mesh,emitters=[emitter],receivers=[receiver],
        optical_profiles=[OpticalProfile(profile_id='measured',reflectance=0,scatter_model='none',bsdf_asset_id=asset['id'])],
        config=RayTraceConfig(ray_count=rays,seed=seed,max_depth=1,min_energy=0,epsilon_mm=1e-7,store_ray_paths=False,contribution_mode='summary'),bsdf_assets=[asset])


def write_map(path, flux):
    coords=(np.arange(GRID)+.5)*WIDTH/GRID-WIDTH/2
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(['u_mm','v_mm','flux_lm'])
        for row,v in enumerate(coords):
            for col,u in enumerate(coords):writer.writerow([u,v,f'{flux[row,col]:.15g}'])


def read_map(path):
    flux=np.full((GRID,GRID),np.nan)
    with path.open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            u,v,p=map(float,(row['u_mm'],row['v_mm'],row['flux_lm']))
            col,row_index=round((u+WIDTH/2)/(WIDTH/GRID)-.5),round((v+WIDTH/2)/(WIDTH/GRID)-.5)
            if not (0<=col<GRID and 0<=row_index<GRID) or not math.isfinite(p) or p<0:raise ValueError('Invalid LT grid/value')
            expected_u=(col+.5)*WIDTH/GRID-WIDTH/2;expected_v=(row_index+.5)*WIDTH/GRID-WIDTH/2
            if abs(u-expected_u)>1e-8 or abs(v-expected_v)>1e-8 or not np.isnan(flux[row_index,col]):raise ValueError('LT grid coordinates/duplicates')
            flux[row_index,col]=p
    if not np.isfinite(flux).all() or flux.sum()<=0:raise ValueError('LT CSV requires all 400 positive-or-zero bins and nonzero total')
    return flux


def scores(actual, expected):
    p,q=actual/actual.sum(),expected/expected.sum()
    return {'flux_error_percent':float((actual.sum()/expected.sum()-1)*100),
            'distribution_overlap_percent':float((1-np.abs(p-q).sum()/2)*100),
            'correlation':float(np.corrcoef(p.ravel(),q.ravel())[0,1])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rays',type=int,default=100000)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs'/'bsdf-validation')
    parser.add_argument('--lt-directory',type=Path,help='Real LT CSVs: <case>.csv; must include provenance.json')
    args=parser.parse_args()
    if args.rays<1000:parser.error('Use at least 1000 rays; report limits are intended for >=100000')
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    cases=[('lambertian',0),('directional',0),('directional',30),('directional',60),('directional',80),('directional',45)]
    assets={kind:make_asset(out/kind,kind=='directional') for kind in ('lambertian','directional')}
    report={'scope':'CPU BSDF vs independent numerical integration; NOT LightTools output',
            'criteria':{'flux_error_percent_max':1.,'distribution_overlap_percent_min':95.,'correlation_min':.99,
                        'emitted_flux_absolute_error_lm_max':1e-10},
            'lt_status':'not_run','cases':[]}
    lt_provenance=None
    if args.lt_directory:
        lt_provenance=json.loads((args.lt_directory/'provenance.json').read_text(encoding='utf-8'))
        required=('engine','version','shift_type','anisotropic','smoothing','rays','source_power_lm')
        if any(k not in lt_provenance for k in required) or lt_provenance['engine']!='LightTools' or lt_provenance['shift_type']!='No Shift' or lt_provenance['anisotropic'] is not False or lt_provenance['smoothing'] is not False or lt_provenance['source_power_lm']!=1:
            raise ValueError('LT provenance must document LightTools, version, No Shift, anisotropic=false, smoothing=false, rays, source_power_lm=1')
        report['lt_provenance']=lt_provenance
    plots=[]
    for kind,angle in cases:
        name=f'{kind}-aoi{angle}';asset,interpolators=assets[kind]
        trace=scene_input(asset,angle,args.rays,7300+angle)
        result=run_direct_ray_trace(trace)
        actual=np.array(result.receiver_grids[0].flux_lumen)
        expected=oracle(interpolators,angle)
        # Convergence of the independent integral is also checked, not presumed.
        refined=oracle(interpolators,angle,2)
        quadrature_delta=float(np.abs(expected-refined).sum()/refined.sum()*100)
        measured=scores(actual,refined)
        emission=result.metrics['_reflection_summary']['lobes']['bsdf']['emitted_flux_lumen']
        row={'case':name,'rays':args.rays,'seed':trace.emitters[0].seed,'received_lm':float(actual.sum()),
             'oracle_received_lm':float(refined.sum()),'emitted_lm':emission,
             'quadrature_L1_delta_percent':quadrature_delta,'runtime_sec':result.runtime_sec,**measured,
             'lt_status':'not_run'}
        row['pass']=bool(abs(measured['flux_error_percent'])<=1 and measured['distribution_overlap_percent']>=95 and measured['correlation']>=.99 and abs(emission-R)<=1e-10 and quadrature_delta<.02)
        write_map(out/f'{name}-sim.csv',actual);write_map(out/f'{name}-oracle.csv',refined)
        # Full request + embedded assets allow reproducing identical settings.
        request={'emitters':[asdict(x) for x in trace.emitters],'receivers':[asdict(x) for x in trace.receivers],
                 'config':asdict(trace.config),'optical_profiles':[x.to_dict() for x in trace.optical_profiles],'bsdf_assets':[asset]}
        (out/f'{name}-request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
        if args.lt_directory and (args.lt_directory/f'{name}.csv').exists():
            lt=read_map(args.lt_directory/f'{name}.csv');row['lt']=scores(actual,lt)
            row['lt_status']='measured_comparison'
            row['lt_pass']=bool(abs(row['lt']['flux_error_percent'])<=1 and row['lt']['distribution_overlap_percent']>=95 and row['lt']['correlation']>=.99)
        report['cases'].append(row);plots.append((name,actual,refined))
        print(json.dumps(row,ensure_ascii=False),flush=True)
    report['oracle_pass']=all(row['pass'] for row in report['cases'])
    if all(row['lt_status']=='measured_comparison' for row in report['cases']):
        report['lt_status']='passed' if all(row['lt_pass'] for row in report['cases']) else 'failed'
    elif any(row['lt_status']=='measured_comparison' for row in report['cases']):report['lt_status']='partial'
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(len(plots),3,figsize=(10,3*len(plots)),layout='constrained')
    for i,(name,actual,expected) in enumerate(plots):
        vmax=max(actual.max(),expected.max())
        for j,(title,data) in enumerate([('Independent integral',expected),('Simulator CPU',actual),('Difference (lm/bin)',actual-expected)]):
            lim=max(abs(actual-expected).max(),1e-12)
            img=axes[i,j].imshow(data,origin='lower',extent=(-10,10,-10,10),cmap='coolwarm' if j==2 else 'viridis',vmin=-lim if j==2 else 0,vmax=lim if j==2 else vmax)
            axes[i,j].set_title(f'{name}\n{title}',fontsize=9);axes[i,j].set_xlabel('Receiver U (mm)');axes[i,j].set_ylabel('Receiver V (mm)')
            fig.colorbar(img,ax=axes[i,j],shrink=.75)
    fig.savefig(out/'comparison.png',dpi=140);plt.close(fig)
    rows=''.join(f"<tr><td>{html.escape(r['case'])}</td><td>{r['flux_error_percent']:+.3f}%</td><td>{r['distribution_overlap_percent']:.2f}%</td><td>{r['correlation']:.5f}</td><td>{'PASS' if r['pass'] else 'FAIL'}</td></tr>" for r in report['cases'])
    page=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>BSDF 연산 검증</title><style>body{{max-width:1050px;margin:40px auto;padding:0 20px;font:17px/1.7 system-ui;color:#172a3a}}table{{border-collapse:collapse;width:100%}}td,th{{padding:9px;border-bottom:1px solid #ccd}}img{{width:100%}}.note{{padding:16px;background:#fff3d8}}</style><h1>BSDF 실제 광선 추적 검증</h1><p>CPU ray tracing과 별도 수치 적분을 비교했다. 합성 산란 표이며 실제 소재 측정값이 아니다.</p><p class="note"><strong>LightTools 매칭 결과가 아니다.</strong> LT 상태: {report['lt_status']}. 실제 LT 데이터가 없으면 매칭률을 계산하지 않는다.</p><p>각 케이스 {args.rays:,} rays. 1 lm 입력, RT 반사율 30%, 550 nm 선택, 등방성 / No Shift. 측정각 0/30/60/80° 및 중간 45°. 판정: 수광량 오차 ≤1%, 정규화 분포 겹침 ≥95%, 상관계수 ≥0.99. LT 비교에도 같은 기준을 사용하되 먼저 난수 수렴을 확인한다.</p><table><tr><th>케이스</th><th>수광량 편차</th><th>분포 겹침</th><th>상관계수</th><th>수치 검증</th></tr>{rows}</table><p>산란 표의 bilinear 보간을 SciPy에서 별도로 구성했다. 반구 midpoint 적분으로 정규화하고, 수광면의 면적 적분으로 기대 광량을 구했다. 적분 해상도를 두 배로 높인 차이도 report.json에 기록했다. 광원 폭 10⁻⁶ mm를 점광원으로 근사한 수치 기준이다.</p><img src="comparison.png" alt="각 입사각의 수치 적분과 시뮬레이터 광분포 비교"><p>상세 수치: <a href="report.json">report.json</a>. 각 CSV의 flux_lm는 bin당 광속이며 조도·휘도 값이 아니다.</p></html>'''
    lt_rows = [row for row in report['cases'] if row.get('lt')]
    if lt_rows:
        table_rows = ''.join(
            f"<tr><td>{html.escape(row['case'])}</td><td>{row['lt']['flux_error_percent']:+.3f}%</td>"
            f"<td>{row['lt']['distribution_overlap_percent']:.2f}%</td><td>{row['lt']['correlation']:.5f}</td>"
            f"<td>{'PASS' if row['lt_pass'] else 'FAIL'}</td></tr>" for row in lt_rows)
        page = page.replace('<strong>LightTools 매칭 결과가 아니다.</strong>',
                            '<strong>위 표는 수치 검증이며, 제공된 LT CSV와의 직접 비교는 아래에 별도로 표시한다.</strong>')
        page = page.replace('</html>', f'<h2>제공된 LightTools 결과와 직접 비교</h2>'
                            f'<p>버전: {html.escape(str(lt_provenance["version"]))}. {len(lt_rows)}/6개 조건.</p>'
                            '<table><tr><th>케이스</th><th>Sim/LT 광량 편차</th><th>분포 겹침</th>'
                            f'<th>상관계수</th><th>LT 비교</th></tr>{table_rows}</table></html>')
    (out/'report.html').write_text(page,encoding='utf-8')
    if not report['oracle_pass'] or report['lt_status']=='failed':return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
