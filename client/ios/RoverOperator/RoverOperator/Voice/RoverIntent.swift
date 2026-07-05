import FoundationModels

/// Structured command schema the on-device Apple Foundation Model fills via guided
/// generation. Kept small and enum-driven so the ~3B on-device model can produce it
/// reliably; open-ended content (small talk, general knowledge) is deliberately NOT
/// modeled here — `needsEscalation` routes that to Claude instead of forcing it through
/// structured generation the on-device model isn't built for.
@Generable
struct RoverIntent {
    @Guide(description: "What the ground crew wants the rover to do")
    var action: RoverAction

    @Guide(description: "Named destination (e.g. 'gate 14', 'jet bridge', 'cart yard') when action is navigate; empty string otherwise")
    var destination: String

    @Guide(description: "True if this requires open-ended conversation the rover can't handle on-device: small talk, general knowledge, or anything not about moving the robot")
    var needsEscalation: Bool
}

@Generable
enum RoverAction: String, CaseIterable {
    case navigate
    case stop
    case greet
    case unknown
}
