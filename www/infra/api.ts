import { bucket } from "./storage";
import { ordersTable } from "./database";

// Stripe webhook handler Lambda
export const api = {
  stripeWebhook: new sst.aws.Function("StripeWebhookHandler", {
    handler: "functions/stripe-webhook.handler",
    timeout: "30 seconds",
    environment: {
      ORDERS_TABLE_NAME: ordersTable.name,
    },
    link: [ordersTable],
    url: true, // Creates a function URL for the webhook
  }),
  
  // Order processing Lambda (triggered by DynamoDB stream or direct invocation)
  orderProcessor: new sst.aws.Function("OrderProcessor", {
    handler: "functions/order-processor.handler",
    timeout: "60 seconds",
    environment: {
      ORDERS_TABLE_NAME: ordersTable.name,
      ASSETS_BUCKET_NAME: bucket.name,
    },
    link: [ordersTable, bucket],
  }),
};

// Secrets (set via `sst secret set` CLI)
export const secrets = {
  stripeSecretKey: new sst.Secret("StripeSecretKey"),
  stripeWebhookSecret: new sst.Secret("StripeWebhookSecret"),
};

// Link secrets to functions
api.stripeWebhook.linkSecret(secrets.stripeSecretKey);
api.stripeWebhook.linkSecret(secrets.stripeWebhookSecret);
