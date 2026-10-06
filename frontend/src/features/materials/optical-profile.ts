import type { MaterialAssignment, OpticalValueOverride } from '@/stores'
import { compileOpticalProfile, type CompiledOpticalProfile } from './material-catalog'
import { inheritedBaseMaterialId } from './surface-assignment-model'

export function resolveOpticalProfile(baseMaterialId: string, surfaceId: string, custom?: OpticalValueOverride): CompiledOpticalProfile {
  const catalog = compileOpticalProfile(baseMaterialId, surfaceId)
  const reflectance = Math.min(1, Math.max(0, custom?.reflectance ?? catalog.reflectance))
  const specular = Math.max(0, custom?.specularRatio ?? catalog.specularRatio)
  const diffuse = Math.max(0, custom?.diffuseRatio ?? catalog.diffuseRatio)
  const total = specular + diffuse
  return {
    reflectance, loss: 1 - reflectance,
    specularRatio: total > 0 ? specular / total : 0,
    diffuseRatio: total > 0 ? diffuse / total : 0,
    scatterModel: custom?.scatterModel ?? catalog.scatterModel,
    roughness: Math.min(1, Math.max(0, custom?.roughness ?? catalog.roughness)),
    scatterSigmaDeg: custom?.scatterSigmaDeg ?? catalog.scatterSigmaDeg,
  }
}

export function resolveAssignmentProfile(assignment: MaterialAssignment, assignments: MaterialAssignment[]) {
  return resolveOpticalProfile(inheritedBaseMaterialId(assignment, assignments), assignment.surfaceId, assignment.opticalOverride)
}

export function faceMaterialAssignment(assignments: MaterialAssignment[], componentId: number, faceId: number) {
  return assignments.findLast((item) => item.enabled && item.componentId === componentId && item.targetType === 'faces' && item.faceIds.includes(faceId))
}

export const unassignedOpticalProfile: CompiledOpticalProfile = {
  reflectance: 0, loss: 1, specularRatio: 0, diffuseRatio: 0,
  scatterModel: 'none', roughness: 1, scatterSigmaDeg: 18,
}

export function isCustomOpticalValid(value: OpticalValueOverride) {
  const unit = (number: number) => Number.isFinite(number) && number >= 0 && number <= 1
  return unit(value.reflectance) && unit(value.loss) && Math.abs(value.reflectance + value.loss - 1) <= 0.001
    && unit(value.specularRatio) && unit(value.diffuseRatio)
    && (value.scatterModel !== 'mixed' || Math.abs(value.specularRatio + value.diffuseRatio - 1) <= 0.001)
    && (value.scatterModel !== 'none' || value.reflectance === 0)
    && (value.roughness === undefined || unit(value.roughness))
    && (value.scatterSigmaDeg === undefined || (Number.isFinite(value.scatterSigmaDeg) && value.scatterSigmaDeg > 0))
}

export function withSurfaceFinish(profile: CompiledOpticalProfile, baseMaterialId: string, surfaceId: string): OpticalValueOverride {
  return { ...compileOpticalProfile(baseMaterialId, surfaceId), reflectance: profile.reflectance, loss: profile.loss }
}
