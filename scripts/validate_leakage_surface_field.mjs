import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
const root=resolve(import.meta.dirname,'..')
const server=await createServer({root:resolve(root,'frontend'),server:{middlewareMode:true},appType:'custom'})
try {
  const {createRayTraceResultSourceContext}=await server.ssrLoadModule('/src/features/raytracing/ray-result-source-context.ts')
  const {buildPrototypeLeakagePreviewData}=await server.ssrLoadModule('/src/features/results/leakage-preview-data.ts')
  const {buildLeakageSurfacePreview}=await server.ssrLoadModule('/src/features/results/leakage-surface-preview.ts')
  const read=async(path)=>JSON.parse(await readFile(path,'utf8'))
  const directory=resolve(root,'outputs/leakage-continuity-validation')
  const rows=[]
  const fields=[]
  for (const name of ['gap_0p5_20k_1lm','gap_0p5_80k_1lm','gap_0p5_80k_0p5lm']) {
    const scene=await read(resolve(directory,name+'.scene.json'))
    const request=await read(resolve(directory,name+'.request.json'))
    const result=await read(resolve(directory,name+'.result.json'))
    result.source_context=createRayTraceResultSourceContext(scene,request,name)
    const t=performance.now()
    const data=buildPrototypeLeakagePreviewData(result,scene)
    const rendered=buildLeakageSurfacePreview(scene,data,request)
    const field=rendered.fields[0]
    if(!field) throw Error('No aperture field: '+name+' '+rendered.reason)
    const positive=Array.from(field.density).filter(v=>v>0)
    rows.push({name,rays:result.total_rays,hits:result.receiver_hit_count,fullCapture:data.coverage.fullCapture,
      samples:data.samples.length,reconstructed:rendered.reconstructedSampleCount,
      fallback:rendered.fallbackSamples.length,apertureAreaMm2:field.mask.open.reduce((a,v)=>a+v,0)*field.mask.stepMm**2,
      positiveAreaMm2:positive.length*field.mask.stepMm**2,inputEnergy:field.totalInputEnergy,
      depositedEnergy:field.totalDepositedEnergy,reconstructionMs:performance.now()-t,
      maxDensity:Math.max(...positive)})
    // World cell coordinates allow comparison across independently padded masks.
    fields.push(new Map(Array.from(field.density,(density,i)=>[
      (field.mask.origin[0]+(i%field.mask.width+.5)*field.mask.stepMm).toFixed(6)+':'+
      (field.mask.origin[1]+(Math.floor(i/field.mask.width)+.5)*field.mask.stepMm).toFixed(6),density])))
    await writeFile(resolve(directory,name+'.bound-result.json'),JSON.stringify(result,null,2))
  }
  const compare=(a,b,scale=1)=>{
    const keys=new Set([...a.keys(),...b.keys()])
    let l1=0,reference=0,maxError=0
    for(const key of keys){const av=(a.get(key)??0)*scale,bv=b.get(key)??0;l1+=Math.abs(av-bv);reference+=Math.abs(av);maxError=Math.max(maxError,Math.abs(av-bv))}
    return {relativeL1:l1/reference,maxDensityDifference:maxError}
  }
  const report={rows,rayCountComparison:compare(fields[0],fields[1]),halfPowerComparison:compare(fields[1],fields[2],.5)}
  await writeFile(resolve(directory,'surface-field-validation.json'),JSON.stringify(report,null,2))
  console.log(JSON.stringify(report,null,2))
} finally {await server.close()}
