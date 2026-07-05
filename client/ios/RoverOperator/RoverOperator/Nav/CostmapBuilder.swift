import Foundation
import ARKit
import RoverNav

/// Turns the ARKit LiDAR scene mesh into a 2D `Costmap` for the RoverNav planner.
/// Vertices in an "obstacle height band" above the floor (i.e. things the rover would
/// hit — walls, furniture legs, people) are projected down onto the ground grid.
enum CostmapBuilder {
    struct Params {
        var resolution = 0.10        // m/cell
        var size = 12.0              // m — square window centered on the robot
        var floorBand = 0.10         // ignore anything below this above the floor
        var ceilingBand = 1.8        // ignore anything above this (ceilings/overheads)
        var inflationRadius = 0.25   // robot radius + safety margin
    }

    /// Build a costmap centered on `center` (nav-plane coords) from the mesh anchors.
    static func build(from meshAnchors: [ARMeshAnchor],
                      center: Vec2,
                      params: Params = Params()) -> Costmap {
        let cells = Int((params.size / params.resolution).rounded())
        let origin = Vec2(center.x - params.size / 2, center.y - params.size / 2)
        var map = Costmap(width: cells, height: cells, resolution: params.resolution, origin: origin)

        // Estimate floor height as the lowest vertex we see (ARKit Y is up).
        var floorY = Float.infinity
        for anchor in meshAnchors {
            floorY = min(floorY, anchor.transform.columns.3.y - 2.0) // rough; refined below
        }

        for anchor in meshAnchors {
            let geom = anchor.geometry
            let verts = geom.vertices
            let vbuf = verts.buffer.contents()
            let t = anchor.transform
            for i in 0..<verts.count {
                let vp = vbuf.advanced(by: verts.offset + verts.stride * i)
                    .assumingMemoryBound(to: (Float, Float, Float).self).pointee
                let local = SIMD4<Float>(vp.0, vp.1, vp.2, 1)
                let world = t * local
                let heightAboveFloor = world.y - floorY
                guard heightAboveFloor > Float(params.floorBand),
                      heightAboveFloor < Float(params.ceilingBand) else { continue }
                map.markObstacle(at: Vec2(Double(world.x), Double(world.z)))
            }
        }

        map.inflate(radius: params.inflationRadius)
        return map
    }
}
