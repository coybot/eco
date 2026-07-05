import SwiftUI

/// Minimal email/password sign-in, matching AuthService's trimmed (Cognito-only) fork.
struct AuthView: View {
    @Environment(AuthService.self) private var authService

    @State private var email = ""
    @State private var password = ""
    @State private var isSignUp = false
    @State private var confirmationCode = ""
    @State private var awaitingConfirmation = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Image(systemName: "figure.wave.circle.fill")
                        .font(.system(size: 56))
                        .frame(maxWidth: .infinity)
                        .foregroundStyle(.tint)
                    Text("Rover Operator")
                        .font(.title2.bold())
                        .frame(maxWidth: .infinity, alignment: .center)
                }
                .listRowBackground(Color.clear)

                if awaitingConfirmation {
                    Section("Check your email") {
                        TextField("Confirmation code", text: $confirmationCode)
                            .keyboardType(.numberPad)
                        Button("Confirm") { Task { await confirm() } }
                    }
                } else {
                    Section {
                        TextField("Email", text: $email)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                        SecureField("Password", text: $password)
                    }

                    Section {
                        Button(isSignUp ? "Sign Up" : "Sign In") {
                            Task { await submit() }
                        }
                        .disabled(email.isEmpty || password.isEmpty || authService.isLoading)

                        Button(isSignUp ? "Already have an account? Sign In" : "Need an account? Sign Up") {
                            isSignUp.toggle()
                        }
                        .font(.footnote)
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.footnote)
                    }
                }
            }
            .disabled(authService.isLoading)
        }
    }

    private func submit() async {
        errorMessage = nil
        do {
            if isSignUp {
                try await authService.signUp(email: email, password: password)
                awaitingConfirmation = true
            } else {
                try await authService.signIn(email: email, password: password)
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func confirm() async {
        errorMessage = nil
        do {
            try await authService.confirmSignUp(email: email, code: confirmationCode)
            awaitingConfirmation = false
            try await authService.signIn(email: email, password: password)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
