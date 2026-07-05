import Foundation

/// Safety layer sitting between the planner and the motors. Independent of the global
/// costmap so it reacts to *dynamic* obstacles (ground crew or a cart crossing its path)
/// and to link loss. Returns whether it is safe to keep driving; if not, the caller must stop.
struct ObstacleGuard {
    var stopDistance: Double = 0.45      // m — hard stop if forward clearance drops below
    var watchdogTimeout = RoverConfig.commsWatchdogTimeout

    enum Decision: Equatable {
        case go
        case stopObstacle(clearance: Double)
        case stopCommsLost
        case stopTipping
    }

    func evaluate(forwardClearance: Double,
                  lastAckAt: Date?,
                  now: Date = Date(),
                  feedback: RoverFeedback?) -> Decision {
        if let fb = feedback, fb.isTipping() { return .stopTipping }
        if forwardClearance < stopDistance { return .stopObstacle(clearance: forwardClearance) }
        if let last = lastAckAt {
            if now.timeIntervalSince(last) > watchdogTimeout { return .stopCommsLost }
        }
        return .go
    }
}
