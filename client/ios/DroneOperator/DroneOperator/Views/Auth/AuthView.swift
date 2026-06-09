import SwiftUI

enum AuthMode { case signIn, signUp, confirm(email: String) }

struct AuthView: View {
    @Environment(AuthService.self) private var authService
    @State private var mode: AuthMode = .signIn
    @State private var email = ""
    @State private var password = ""
    @State private var confirmPassword = ""
    @State private var confirmationCode = ""

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 0) {
                    Spacer().frame(height: geometry.size.height * 0.12)
                    heroSection
                    Spacer().frame(height: 40)
                    formSection
                        .padding(.horizontal, 24)
                    Spacer().frame(height: 40)
                }
                .frame(minHeight: geometry.size.height)
            }
        }
        .background(
            LinearGradient(
                colors: [Color(red: 0.05, green: 0.05, blue: 0.15),
                         Color(red: 0.1, green: 0.1, blue: 0.2)],
                startPoint: .top, endPoint: .bottom
            ).ignoresSafeArea()
        )
    }

    // MARK: - Hero

    private var heroSection: some View {
        VStack(spacing: 20) {
            ZStack {
                Circle()
                    .fill(RadialGradient(colors: [Color.cyan.opacity(0.3), Color.clear],
                                        center: .center, startRadius: 20, endRadius: 100))
                    .frame(width: 200, height: 200)
                Image("DroneIcon")
                    .resizable().aspectRatio(contentMode: .fit)
                    .frame(width: 120, height: 120)
                    .clipShape(RoundedRectangle(cornerRadius: 24))
            }
            VStack(spacing: 8) {
                Text("Drone Operator")
                    .font(.system(size: 36, weight: .bold, design: .rounded))
                    .foregroundColor(.white)
                Text("Command your fleet")
                    .font(.body).foregroundColor(.white.opacity(0.6))
            }
        }
    }

    // MARK: - Form

    @ViewBuilder
    private var formSection: some View {
        switch mode {
        case .signIn:    signInForm
        case .signUp:    signUpForm
        case .confirm(let e): confirmForm(email: e)
        }
    }

    private var signInForm: some View {
        VStack(spacing: 14) {
            // Google
            socialButton(label: "Continue with Google", systemImage: "g.circle.fill") {
                Task { try? await authService.signInWithGoogle() }
            }

            divider

            emailField
            passwordField

            if let error = authService.error {
                errorText(error.localizedDescription)
            }

            primaryButton(label: "Sign In", loading: authService.isLoading,
                          disabled: email.isEmpty || password.isEmpty) {
                Task { try? await authService.signIn(email: email, password: password) }
            }

            toggleButton(label: "Don't have an account? ", action: "Sign up") {
                withAnimation { mode = .signUp }
            }
        }
    }

    private var signUpForm: some View {
        VStack(spacing: 14) {
            // Google
            socialButton(label: "Continue with Google", systemImage: "g.circle.fill") {
                Task { try? await authService.signInWithGoogle() }
            }

            divider

            emailField
            passwordField

            SecureField("Confirm Password", text: $confirmPassword)
                .styledInput()

            if let error = authService.error {
                errorText(error.localizedDescription)
            }

            primaryButton(label: "Create Account", loading: authService.isLoading,
                          disabled: email.isEmpty || password.isEmpty || confirmPassword.isEmpty) {
                guard password == confirmPassword else {
                    return // passwords don't match — could show inline error
                }
                Task {
                    try? await authService.signUp(email: email, password: password)
                    if authService.error == nil {
                        withAnimation { mode = .confirm(email: email) }
                    }
                }
            }

            toggleButton(label: "Already have an account? ", action: "Sign in") {
                withAnimation { mode = .signIn }
            }
        }
    }

    private func confirmForm(email: String) -> some View {
        VStack(spacing: 14) {
            Text("Check your email")
                .font(.headline).foregroundColor(.white)
            Text("We sent a verification code to \(email)")
                .font(.subheadline).foregroundColor(.white.opacity(0.6))
                .multilineTextAlignment(.center)

            TextField("Verification Code", text: $confirmationCode)
                .keyboardType(.numberPad)
                .styledInput()

            if let error = authService.error {
                errorText(error.localizedDescription)
            }

            primaryButton(label: "Verify", loading: authService.isLoading,
                          disabled: confirmationCode.isEmpty) {
                Task {
                    try? await authService.confirmSignUp(email: email, code: confirmationCode)
                    if authService.error == nil {
                        // Auto sign-in after confirmation
                        try? await authService.signIn(email: email, password: password)
                    }
                }
            }

            toggleButton(label: "Wrong email? ", action: "Go back") {
                withAnimation { mode = .signUp }
            }
        }
    }

    // MARK: - Reusable components

    private var emailField: some View {
        TextField("Email", text: $email)
            .keyboardType(.emailAddress)
            .autocapitalization(.none)
            .autocorrectionDisabled()
            .styledInput()
    }

    private var passwordField: some View {
        SecureField("Password", text: $password)
            .styledInput()
    }

    private var divider: some View {
        HStack {
            Rectangle().frame(height: 1).foregroundColor(.white.opacity(0.15))
            Text("or").font(.caption).foregroundColor(.white.opacity(0.4)).padding(.horizontal, 8)
            Rectangle().frame(height: 1).foregroundColor(.white.opacity(0.15))
        }
    }

    private func socialButton(label: String, systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: systemImage).font(.title2)
                Text(label).font(.system(size: 17, weight: .semibold))
            }
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(Color.white)
            .foregroundColor(.black)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .disabled(authService.isLoading)
    }

    private func primaryButton(label: String, loading: Bool, disabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Group {
                if loading { ProgressView().tint(.black) }
                else { Text(label).font(.system(size: 17, weight: .semibold)) }
            }
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(Color.cyan)
            .foregroundColor(.black)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .disabled(loading || disabled)
    }

    private func toggleButton(label: String, action: String, onTap: @escaping () -> Void) -> some View {
        Button(action: onTap) {
            (Text(label).foregroundColor(.white.opacity(0.6)) +
             Text(action).foregroundColor(.cyan))
            .font(.subheadline)
        }
    }

    private func errorText(_ msg: String) -> some View {
        Text(msg)
            .font(.footnote)
            .foregroundColor(.red.opacity(0.9))
            .multilineTextAlignment(.center)
            .padding(.horizontal, 4)
    }
}

// MARK: - View Modifier

private struct StyledInputModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding()
            .background(Color.white.opacity(0.1))
            .foregroundColor(.white)
            .tint(.white)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.white.opacity(0.2)))
    }
}

private extension View {
    func styledInput() -> some View { modifier(StyledInputModifier()) }
}

#Preview {
    AuthView().environment(AuthService.shared)
}
