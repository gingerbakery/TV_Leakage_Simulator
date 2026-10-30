import { expect, it } from 'vitest'
import { createViewAngleReceiver, defaultReceiverView, expandReceiverView, receiverViewAnglePairs, receiverViewDirection } from './receiver-view'
import { buildRayTraceRequest } from './ray-tracing-model'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createWorkspaceStore } from '@/stores'
import { createBitsamProject, serializeBitsamProject, parseBitsamProject } from '@/features/projects/bitsam-project'

const dot = (a: number[], b: number[]) => a.reduce((sum, value, i) => sum+value*b[i],0)

it('matches observer directions and places an inward-facing plane outside every CAD corner', () => {
  const scene = createSceneFixture()
  for (const axis of ['pos_x','neg_x','pos_y','neg_y','pos_z','neg_z'] as const) {
    for (const [horizontal_deg,vertical_deg] of [[0,0],[45,20],[89.9,-89.9]]) {
      const view = { ...defaultReceiverView, axis, horizontal_deg, vertical_deg }
      const receiver = createViewAngleReceiver('r',scene,[30,30,10],view,3)
      const outward = receiverViewDirection(view)
      expect(dot(receiver.normal,outward)).toBeCloseTo(-1,12)
      expect(dot(receiver.u_axis!,receiver.v_axis!)).toBeCloseTo(0,12)
      expect(dot(receiver.normal,receiver.u_axis!)).toBeCloseTo(0,12)
      expect(dot(receiver.normal,receiver.v_axis!)).toBeCloseTo(0,12)
      for (const component of scene.components) for (const x of [component.bbox_min[0],component.bbox_max[0]]) for (const y of [component.bbox_min[1],component.bbox_max[1]]) for (const z of [component.bbox_min[2],component.bbox_max[2]]) {
        expect(dot(receiver.center,outward)-dot([x,y,z],outward)).toBeGreaterThanOrEqual(3-1e-9)
      }
    }
  }
  const rear = createViewAngleReceiver('r',scene,[30,30,10],defaultReceiverView,3)
  expect(rear.center).toEqual([30,30,-3])
  expect(rear.normal).toEqual([-0,-0,1])
  const view = receiverViewDirection({ ...defaultReceiverView, horizontal_deg: 30, vertical_deg: 20 })
  expect(view[0]).toBeCloseTo(Math.sin(Math.PI/6)*Math.cos(Math.PI/9))
  expect(view[1]).toBeCloseTo(Math.sin(Math.PI/9))
  expect(view[2]).toBeCloseTo(-Math.cos(Math.PI/6)*Math.cos(Math.PI/9))
})

it('keeps sensor size, grid, acceptance and original settings unchanged across angle runs', () => {
  const scene = createSceneFixture()
  const receiver = { ...createViewAngleReceiver('r',scene,[30,30,10],{ ...defaultReceiverView, range_deg:1 },3), width_mm:12, height_mm:8, resolution:[6,4] as [number,number], acceptance_angle_deg:60 }
  const before = structuredClone(receiver)
  const views = expandReceiverView(receiver,scene)
  expect(views).toHaveLength(9)
  expect(views.map((item) => [item.view_angles!.horizontal_deg,item.view_angles!.vertical_deg])).toContainEqual([0,0])
  for (const view of views) expect(view).toMatchObject({ width_mm:12,height_mm:8,resolution:[6,4],acceptance_angle_deg:60,view_angles:{range_deg:0} })
  expect(receiver).toEqual(before)
  expect(() => receiverViewAnglePairs({ ...defaultReceiverView,range_deg:10,step_deg:.1 })).toThrow('121')
})

it('traces resolved single planes without sending UI fields and retains outside geometry for ROI', () => {
  const scene = createSceneFixture(), fixture = createRayTraceResultFixture()
  const receiver = createViewAngleReceiver('r',scene,[30,30,10],defaultReceiverView,3)
  const options = { scene, projectName:'Angle',emitters:fixture.emitters,receivers:[receiver],materialAssignments:[],transformRules:[],excludedComponentIds:[],deletedComponentIds:[],roiScopes:[],config:fixture.config }
  const request = buildRayTraceRequest(options)
  expect(request.observer_probe).toBeUndefined()
  expect(request.receivers[0]).not.toHaveProperty('view_angles')
  const scoped = buildRayTraceRequest({ ...options, roiScopes: [{ id:'roi',scopeId:'ROI',source:'box',view:'coordinate',active:true,
    clipBox:{plane:'xyz',xMin:0,xMax:1,yMin:0,yMax:1,zMin:0,zMax:1},
    components:[{componentId:1,componentName:'Part',faceIds:[0],areaMm2:1,bboxMin:{x:0,y:0,z:0},bboxMax:{x:1,y:1,z:1}}] }] })
  expect(scoped.roi_faces).toBeUndefined()
  expect(scoped.analysis_roi?.face_indices).toEqual([0])

  expect(() => buildRayTraceRequest({ ...options,receivers:[{ ...receiver,view_angles:{ ...defaultReceiverView,range_deg:1 } }] })).toThrow('각도별')
})

it('persists angle placement and sweep settings and rejects invalid settings', () => {
  const scene = createSceneFixture(), store = createWorkspaceStore()
  store.getState().actions.addCadCase({path:'angle.step',displayName:'Angle'})
  const receiver = createViewAngleReceiver('r',scene,[30,30,10],{ ...defaultReceiverView,range_deg:2,step_deg:1 },3)
  store.getState().actions.upsertReceiver(receiver)
  const project = createBitsamProject(scene,store.getState())
  const restored = parseBitsamProject(serializeBitsamProject(project))
  expect(restored.workspace.receivers[0]).toEqual(JSON.parse(JSON.stringify(receiver)))
  store.getState().actions.restoreProjectState(restored.workspace)
  expect(store.getState().receivers[0].view_angles).toEqual(receiver.view_angles)
  restored.workspace.receivers[0].view_angles!.horizontal_deg=91
  expect(() => parseBitsamProject(serializeBitsamProject(restored))).toThrow()
})
