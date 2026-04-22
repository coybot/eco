import SwiftUI
import AuthenticationServices

struct AuthView: View {
    @Environment(AuthService.self) private var authService
    @State private var showError = false
    @State private var errorMessage = ""
    
    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                Spacer()
                
                // Hero section
                heroSection
                
                Spacer()
                
                // Sign in buttons
                signInButtons
                    .padding(.horizontal, 24)
                    .padding(.bottom, 60)
            }
            .frame(minHeight: geometry.size.height)
        }
        .background(
            LinearGradient(
                colors: [
                    Color(red: 0.05, green: 0.05, blue: 0.15),
                    Color(red: 0.1, green: 0.1, blue: 0.2)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
        )
        .alert("Error", isPresented: $showError) {
            Button("OK") {
                showError = false
            }
        } message: {
            Text(errorMessage)
        }
    }
    
    // MARK: - Hero Section
    
    private var heroSection: some View {
        VStack(spacing: 20) {
            // Drone icon with glow
            ZStack {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [
                                Color.cyan.opacity(0.3),
                                Color.clear
                            ],
                            center: .center,
                            startRadius: 20,
                            endRadius: 100
                        )
                    )
                    .frame(width: 200, height: 200)
                
                Image("DroneIcon")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 120, height: 120)
                    .clipShape(RoundedRectangle(cornerRadius: 24))
            }
            
            VStack(spacing: 8) {
                Text("Drone Operator")
                    .font(.system(size: 36, weight: .bold, design: .rounded))
                    .foregroundColor(.white)
                
                Text("Command your fleet")
                    .font(.body)
                    .foregroundColor(.white.opacity(0.6))
            }
        }
    }
    
    // MARK: - Sign In Buttons
    
    private var signInButtons: some View {
        VStack(spacing: 14) {
            // Sign in with Apple - custom styled button to avoid UIColor warning
            Button {
                        Task {
                            do {
                                try await authService.signInWithApple()
                            } catch {
                        error.logAsUIError(context: "Sign in with Apple")
                                errorMessage = error.localizedDescription
                                showError = true
                            }
                        }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "apple.logo")
                        .font(.title2)
                    Text("Sign in with Apple")
                        .font(.system(size: 17, weight: .semibold))
                }
                .frame(maxWidth: .infinity)
                .frame(height: 54)
                .background(Color.white)
                .foregroundColor(.black)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .disabled(authService.isLoading)
            
            // Sign in with Google (only shown if configured)
            if !AWSConfig.googleClientId.isEmpty {
                Button {
                    Task {
                        do {
                            try await authService.signInWithGoogle()
                        } catch {
                            error.logAsUIError(context: "Sign in with Google")
                            errorMessage = error.localizedDescription
                            showError = true
                        }
                    }
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "g.circle.fill")
                            .font(.title2)
                        Text("Sign in with Google")
                            .font(.system(size: 17, weight: .semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 54)
                    .background(Color.white)
                    .foregroundColor(.black)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                }
                .disabled(authService.isLoading)
            }
            
            // Loading indicator
            if authService.isLoading {
                ProgressView()
                    .tint(.cyan)
                    .padding(.top, 16)
            }
        }
    }
}

#Preview {
    AuthView()
        .environment(AuthService.shared)
}
