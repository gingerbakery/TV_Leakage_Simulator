export interface ViewerScreenPoint {
  x: number
  y: number
  z?: number
}

/** Returns an adaptive grid of screen points for visible-surface discovery.
 * A fixed center/corner probe misses small faces and gaps between components;
 * the bounded grid remains predictable while covering the complete drag box. */
export function screenBoxSamplePoints(
  box: { minX: number; maxX: number; minY: number; maxY: number },
  targetSpacing = 24,
  maxSamplesPerAxis = 25,
): Array<[number, number]> {
  const width = Math.max(0, box.maxX - box.minX)
  const height = Math.max(0, box.maxY - box.minY)
  const countFor = (span: number) => {
    const requested = Math.max(3, Math.ceil(span / Math.max(1, targetSpacing)) + 1)
    const bounded = Math.min(Math.max(3, maxSamplesPerAxis), requested)
    // An odd count always probes the exact center as well as both edges.
    return bounded % 2 === 0 && bounded < maxSamplesPerAxis ? bounded + 1 : bounded
  }
  const columns = countFor(width)
  const rows = countFor(height)
  const points: Array<[number, number]> = []
  for (let row = 0; row < rows; row += 1) {
    const y = box.minY + height * row / Math.max(1, rows - 1)
    for (let column = 0; column < columns; column += 1) {
      const x = box.minX + width * column / Math.max(1, columns - 1)
      points.push([x, y])
    }
  }
  return points
}

function pointInsideBox(
  point: ViewerScreenPoint,
  minX: number,
  maxX: number,
  minY: number,
  maxY: number,
): boolean {
  return point.x >= minX && point.x <= maxX &&
    point.y >= minY && point.y <= maxY
}

function segmentsIntersect(
  a: ViewerScreenPoint,
  b: ViewerScreenPoint,
  c: ViewerScreenPoint,
  d: ViewerScreenPoint,
): boolean {
  if (
    Math.max(a.x, b.x) < Math.min(c.x, d.x) ||
    Math.max(c.x, d.x) < Math.min(a.x, b.x) ||
    Math.max(a.y, b.y) < Math.min(c.y, d.y) ||
    Math.max(c.y, d.y) < Math.min(a.y, b.y)
  ) return false
  const cross = (
    first: ViewerScreenPoint,
    second: ViewerScreenPoint,
    third: ViewerScreenPoint,
  ) => (second.x - first.x) * (third.y - first.y) -
    (second.y - first.y) * (third.x - first.x)
  const abC = cross(a, b, c)
  const abD = cross(a, b, d)
  const cdA = cross(c, d, a)
  const cdB = cross(c, d, b)
  return abC * abD <= 0 && cdA * cdB <= 0
}

export function screenTriangleIntersectsBox(
  triangle: [ViewerScreenPoint, ViewerScreenPoint, ViewerScreenPoint],
  box: { minX: number; maxX: number; minY: number; maxY: number },
): boolean {
  if (triangle.some((point) => pointInsideBox(
    point,
    box.minX,
    box.maxX,
    box.minY,
    box.maxY,
  ))) return true

  const corners: [ViewerScreenPoint, ViewerScreenPoint, ViewerScreenPoint, ViewerScreenPoint] = [
    { x: box.minX, y: box.minY },
    { x: box.maxX, y: box.minY },
    { x: box.maxX, y: box.maxY },
    { x: box.minX, y: box.maxY },
  ]
  const triangleSign = (
    point: ViewerScreenPoint,
    first: ViewerScreenPoint,
    second: ViewerScreenPoint,
  ) => (point.x - second.x) * (first.y - second.y) -
    (first.x - second.x) * (point.y - second.y)
  const pointInsideTriangle = (point: ViewerScreenPoint) => {
    const first = triangleSign(point, triangle[0], triangle[1])
    const second = triangleSign(point, triangle[1], triangle[2])
    const third = triangleSign(point, triangle[2], triangle[0])
    return !(
      (first < 0 || second < 0 || third < 0) &&
      (first > 0 || second > 0 || third > 0)
    )
  }
  if (corners.some(pointInsideTriangle)) return true

  const triangleEdges = [
    [triangle[0], triangle[1]],
    [triangle[1], triangle[2]],
    [triangle[2], triangle[0]],
  ] as const
  const boxEdges = [
    [corners[0], corners[1]],
    [corners[1], corners[2]],
    [corners[2], corners[3]],
    [corners[3], corners[0]],
  ] as const
  return triangleEdges.some(([start, end]) =>
    boxEdges.some(([boxStart, boxEnd]) =>
      segmentsIntersect(start, end, boxStart, boxEnd),
    ),
  )
}
