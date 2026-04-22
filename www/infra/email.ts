// SES email configuration for transactional emails
// Note: SES requires domain verification before use

export const email = {
  // Email identity for sending (configure domain in AWS Console first)
  // This is a placeholder - actual domain verification happens in AWS Console
  fromEmail: "orders@astral.us",
  
  // Email templates would be created via AWS Console or SDK
  // Common templates:
  // - order-confirmation
  // - shipping-notification
  // - welcome-email
};

// SES sending configuration
export const sesConfig = {
  region: "us-east-1", // SES region
  fromAddress: email.fromEmail,
};
