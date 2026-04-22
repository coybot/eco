# Contributing to Astral Drone Platform

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## License

By contributing to this project, you agree that your contributions will be licensed under the Apache License 2.0.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/astral-drone.git`
3. Create a branch: `git checkout -b feature/your-feature`
4. Make your changes
5. Test your changes
6. Submit a pull request

## Development Setup

### Drone Code (Python)

```bash
cd drone/common
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### iOS App

```bash
cd client/ios/DroneOperator
xcodegen generate
open DroneOperator.xcodeproj
```

### AWS Backend

```bash
cd aws
sam build
sam deploy --guided
```

## Code Style

- **Python**: Follow PEP 8, use type hints
- **Swift**: Follow Swift API Design Guidelines
- **YAML**: 2-space indentation

## Testing

Before submitting a PR:

1. Test on actual hardware if modifying drone code
2. Run the iOS app on a real device (not just simulator) for MQTT features
3. Deploy AWS changes to a test stack first

## Pull Request Process

1. Update documentation if needed
2. Add tests for new features
3. Ensure CI passes
4. Request review from maintainers

## Areas We Need Help

- **Hardware support**: New flight controllers, cameras, companion computers
- **Navigation**: Improved obstacle avoidance, SLAM algorithms
- **AI/ML**: Better on-device reasoning, new perception capabilities
- **Mobile**: Android app (none exists yet!)
- **Documentation**: Tutorials, examples, translations

## Code of Conduct

Be respectful and constructive. We're building something cool together.

## Questions?

Open an issue or join our Discord (link TBD).
