"""Package the synthetic benchmark settings as portable .bitsam + a STEP plane.

Run validate_bsdf_transport.py or validate_bsdf_gpu.py first. Requires OCP.
These are calibration fixtures, never measured production materials.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leakage_simulator.api import ApiRuntime
from leakage_simulator.bitsam_package import write_package


def fingerprint(scene):
    def js_round(value):
        rounded=round(float(value),6)
        return int(rounded) if rounded.is_integer() else rounded
    signature=[{'component_id':c['component_id'],'object_id':c['object_id'],'face_count':c['face_count'],
                'bbox_min':[js_round(x) for x in c['bbox_min']],'bbox_max':[js_round(x) for x in c['bbox_max']]}
               for c in sorted(scene['components'],key=lambda c:c['component_id'])]
    hash_=0x811c9dc5
    for char in json.dumps(signature,separators=(',',':')):
        hash_=((hash_^ord(char))*0x01000193)&0xffffffff
    return {'scene_schema_version':scene['schema_version'],'face_count':scene['metadata']['face_count'],
            'vertex_count':scene['metadata']['vertex_count'],'component_count':scene['metadata']['component_count'],
            'geometry_signature':f'fnv1a32:{hash_:08x}'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'outputs'/'bsdf-validation')
    parser.add_argument('--backend',choices=('cpu','gpu_cuda'),default='cpu')
    args=parser.parse_args();out=args.directory.resolve()
    is_gpu=args.backend=='gpu_cuda'
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pnt
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    polygon=BRepBuilderAPI_MakePolygon()
    for p in [(-100,-100,0),(100,-100,0),(100,100,0),(-100,100,0)]:polygon.Add(gp_Pnt(*p))
    polygon.Close();face=BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    writer=STEPControl_Writer();writer.Transfer(face,STEPControl_AsIs)
    source=out/'test-plane.step'
    if writer.Write(str(source))!=IFSelect_RetDone:raise RuntimeError('STEP export failed')
    runtime=ApiRuntime(out/'runtime')
    scene=runtime.load_scene(str(source));token=scene['metadata']['scene_token']
    for path in sorted(out.glob('*-request.json')):
        name=path.name.removesuffix('-request.json');request=json.loads(path.read_text(encoding='utf-8'))
        if request['config']['compute_backend'] != args.backend:
            raise ValueError(f'{name}: request backend does not match {args.backend}')
        receiver={**request['receivers'][0],'receiver_type':'rectangle','placement_mode':'datum_plane','display_name':'Screen',
                  'reference_mode':None,'reference_vertex_indices':[],'reference_edge_vertex_indices':[],
                  'reference_vertex_points':[],'reference_edge_points':[],'view_distance_mm':None,
                  'base_center':None,'base_u_axis':None,'base_v_axis':None,'base_normal':None,
                  'position_offset_mm':[0,0,0],'tilt_xyz_deg':[0,0,0],'pivot':None}
        assignments=[{'assignmentId':f'material-part-{c["component_id"]}','componentId':c['component_id'],'targetType':'part','faceIds':[],
                      'baseMaterialId':'pc_black','surfaceId':'matte_black_resin','profileId':'','bsdfAssetId':request['bsdf_assets'][0]['id'],
                      'opticalOverride':{'reflectance':0,'loss':1,'specularRatio':0,'diffuseRatio':1},'enabled':True} for c in scene['components']]
        workspace={'hiddenComponentIds':[],'excludedComponentIds':[],'deletedComponentIds':[],'componentNameOverrides':{},
                   'componentColorOverrides':{},'faceColorOverrides':[],'materialAssignments':assignments,'customOpticalProfiles':[],
                   'transformRules':[],'roiScopes':[],'roiScopeSequence':0,'bsdfAssets':request['bsdf_assets'],
                   'emitters':request['emitters'],'receivers':[receiver],'rayTraceConfig':request['config'],
                   'rayPathDisplayFilters':{'receiver_direct':True,'receiver_reflected':True,'direct':False,'specular':False,'lambertian':False,'gaussian':False,'bsdf':False}}
        project={'format':'tv-leakage-simulator-project','schema_version':'bitsam-project.v1-bsdf1','application_version':'1.0.0',
                 'saved_at':datetime.now(timezone.utc).isoformat(),'project_name':name,
                 'cad':{'display_name':source.name,'file_extension':'.step','fingerprint':fingerprint(scene)},
                 'case_metadata':{'name':name,'note':f'Synthetic {args.backend} BSDF calibration. NOT measured material. LT matching pending.'},
                 'workspace':workspace,'analysis_result':None}
        # Resolve the actual imported CAD trace geometry before portable export.
        request['scene_token']=token
        runtime._build_trace_input_for_request(runtime._scene_mesh_for_request(request),request)
        write_package(out/f'{name}.bitsam',project,source,scene,runtime._scene_mesh_for_request(request))
        print(out/f'{name}.bitsam')
    (out/'scene.json').write_text(json.dumps(scene,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    guide='bsdf-gpu-validation.md' if is_gpu else 'bsdf-transport-validation.md'
    (out/'README.md').write_text((ROOT/'docs'/guide).read_text(encoding='utf-8'),encoding='utf-8')
    for guide in ('bsdf-transport-validation.md','bsdf-gpu-validation.md'):
        if (ROOT/'docs'/guide).exists():
            (out/guide).write_text((ROOT/'docs'/guide).read_text(encoding='utf-8'),encoding='utf-8')
    (out/'bsdf-import-foundation.md').write_text((ROOT/'docs'/'bsdf-import-foundation.md').read_text(encoding='utf-8'),encoding='utf-8')
    (out/'compare_bsdf_lt_csv.py').write_text((ROOT/'tools'/'compare_bsdf_lt_csv.py').read_text(encoding='utf-8'),encoding='utf-8')
    # Only our public synthetic calibration inputs/results; no runtime uploads/logs.
    plot='gpu-heatmaps.png' if is_gpu else 'comparison.png'
    entries=[out/name for name in ('README.md','bsdf-import-foundation.md','bsdf-transport-validation.md','bsdf-gpu-validation.md','report.html','report.json',plot,'test-plane.step','compare_bsdf_lt_csv.py') if (out/name).exists()]
    for request_path in sorted(out.glob('*-request.json')):
        name=request_path.name.removesuffix('-request.json')
        if is_gpu:
            # The company-side comparison tool uses the backend-neutral -sim name.
            (out/f'{name}-sim.csv').write_bytes((out/f'{name}-gpu.csv').read_bytes())
        entries += [out/f'{name}{suffix}' for suffix in ('.bitsam','-request.json','-sim.csv','-oracle.csv')]
    for kind in ('lambertian','directional'):
        entries += sorted((out/kind).glob('*.txt'))
    entries += [out/name for name in ('ui-validation.json','ui-result.png','ui-material.png') if (out/name).exists()]
    if is_gpu:
        entries += [out/name for name in ('gpu-core-accuracy.json','gpu-resident-regression.json',
            'gpu-accumulator-regression.json','gpu-compact-regression.json') if (out/name).exists()]
    archive=out.parent/('bsdf-gpu-calibration-20261014.zip' if is_gpu else 'bsdf-calibration-20261014.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for entry in entries:bundle.write(entry,entry.relative_to(out).as_posix())
    print(archive)


if __name__=='__main__':main()
