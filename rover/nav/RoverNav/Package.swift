// swift-tools-version: 6.0
import PackageDescription

// RoverNav — platform-independent navigation core for the iOS-brained WAVE ROVER.
//
// Contains only Foundation math (no ARKit/UIKit) so it can be:
//   • unit-tested off-device with `swift test`, and
//   • embedded into the RoverOperator iOS app as a local package dependency.
//
// The iOS app feeds this package a Costmap built from the ARKit/LiDAR scene mesh
// and the live ARKit pose; the package returns a path and the left/right wheel
// velocities to stream to the WAVE ROVER ESP32 as {"T":1,"L":..,"R":..}.
let package = Package(
    name: "RoverNav",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RoverNav", targets: ["RoverNav"]),
    ],
    targets: [
        .target(name: "RoverNav"),
        .testTarget(name: "RoverNavTests", dependencies: ["RoverNav"]),
    ]
)
