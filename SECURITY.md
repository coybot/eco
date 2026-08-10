# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's [private vulnerability
reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
— the **Security** tab on this repository, then **Report a vulnerability**. That opens a
private advisory visible only to you and the maintainers.

Include what you have: affected component and version or commit, how to reproduce, and what
an attacker gains. A rough report sent early is more useful than a polished one sent late.

## Response

This is a small project, so expect maintainer-effort timelines rather than enterprise SLAs:

| Stage | Target |
|---|---|
| Acknowledgement | 3 business days |
| Initial assessment | 10 business days |
| Fix or documented mitigation | depends on severity; we will tell you the plan |

We will credit you in the advisory unless you prefer otherwise. There is no bug bounty.

## This software controls aircraft

Treat this as different from a typical web project. Code here commands flight controllers over
MAVLink, runs autonomy on a companion computer, and accepts commands from a cloud backend. A
defect can cause an aircraft to behave unexpectedly, which is a **physical safety risk to
people and property**, not only a data risk.

Findings we consider especially serious:

- Anything permitting unauthorized command or control of a vehicle
- Bypass of the safety gate in `drone/common/daemon.py` that blocks generated code from
  invoking disarm-in-flight and similar destructive primitives
- Sandbox escape in the execution path for model-generated code
- Authentication or authorization flaws in the API, IoT topic permissions, or fleet
  provisioning
- Anything letting one operator reach another operator's vehicle
- Supply-chain exposure in the one-line installer or the artifacts it fetches

## Scope

**In scope:** this repository's source, its deployment templates, and the installer.

**Out of scope:** third-party dependencies (report upstream, and tell us so we can pin or
patch), findings that require physical access to an already-trusted companion computer, and
issues in the simulator that cannot affect real flight.

## Operator guidance

If you deploy this, note that the defaults are development defaults, not hardened ones:

- Change every default device credential before a vehicle leaves your bench.
- Provisioning brings up an **open** WiFi access point by design. Provision somewhere you
  control, not in the field.
- Never fly with propellers fitted while testing motor commands. See the safety notes in the
  README.

## Reporting a safety defect that is not a security vulnerability

If you find behavior that is unsafe but not attacker-triggered — a failsafe that does not
fire, a mode transition that drops control authority — please still report it privately first.
We would rather coordinate a fix than have it disclosed on a timeline that leaves flying
aircraft on a bad build.
