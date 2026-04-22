import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, PutCommand } from "@aws-sdk/lib-dynamodb";
import Stripe from "stripe";

// SST Resource type (generated at deploy time)
declare const Resource: {
  StripeSecretKey: { value: string };
  StripeWebhookSecret: { value: string };
  OrdersTable: { name: string };
};

const dynamoClient = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(dynamoClient);

// Use SST Resource in Lambda, fallback to env vars for local dev
const stripeSecretKey = typeof Resource !== "undefined" 
  ? Resource.StripeSecretKey.value 
  : process.env.STRIPE_SECRET_KEY!;

const stripe = new Stripe(stripeSecretKey, {
  apiVersion: "2025-01-27.acacia",
});

interface WebhookEvent {
  body: string;
  headers: Record<string, string>;
}

export async function handler(event: WebhookEvent) {
  const signature = event.headers["stripe-signature"];

  if (!signature) {
    return {
      statusCode: 400,
      body: JSON.stringify({ error: "No signature" }),
    };
  }

  let stripeEvent: Stripe.Event;

  const webhookSecret = typeof Resource !== "undefined"
    ? Resource.StripeWebhookSecret.value
    : process.env.STRIPE_WEBHOOK_SECRET!;

  try {
    stripeEvent = stripe.webhooks.constructEvent(
      event.body,
      signature,
      webhookSecret
    );
  } catch (err) {
    console.error("Webhook signature verification failed:", err);
    return {
      statusCode: 400,
      body: JSON.stringify({ error: "Invalid signature" }),
    };
  }

  // Handle the event
  switch (stripeEvent.type) {
    case "checkout.session.completed": {
      const session = stripeEvent.data.object as Stripe.Checkout.Session;
      
      // Create order record in DynamoDB
      const orderId = session.id;
      const timestamp = new Date().toISOString();
      const items = session.metadata?.items ? JSON.parse(session.metadata.items) : [];
      
      const tableName = typeof Resource !== "undefined"
        ? Resource.OrdersTable.name
        : process.env.ORDERS_TABLE_NAME!;

      // Store order metadata
      await docClient.send(new PutCommand({
        TableName: tableName,
        Item: {
          pk: `ORDER#${orderId}`,
          sk: "METADATA",
          gsi1pk: `CUSTOMER#${session.customer_details?.email}`,
          gsi1sk: `ORDER#${orderId}`,
          orderId,
          status: "confirmed",
          customerEmail: session.customer_details?.email,
          customerName: session.customer_details?.name,
          shippingAddress: session.shipping_details?.address,
          amountTotal: session.amount_total,
          currency: session.currency,
          items,
          createdAt: timestamp,
          updatedAt: timestamp,
        },
      }));

      // Store individual line items
      for (const item of items) {
        await docClient.send(new PutCommand({
          TableName: tableName,
          Item: {
            pk: `ORDER#${orderId}`,
            sk: `ITEM#${item.productId}`,
            productId: item.productId,
            quantity: item.quantity,
            createdAt: timestamp,
          },
        }));
      }

      console.log("Order created:", orderId);
      
      // TODO: Trigger email notification
      // TODO: Trigger inventory update
      
      break;
    }

    case "checkout.session.expired": {
      const session = stripeEvent.data.object as Stripe.Checkout.Session;
      console.log("Checkout session expired:", session.id);
      break;
    }

    case "payment_intent.payment_failed": {
      const paymentIntent = stripeEvent.data.object as Stripe.PaymentIntent;
      console.log("Payment failed:", paymentIntent.id);
      break;
    }

    default:
      console.log(`Unhandled event type: ${stripeEvent.type}`);
  }

  return {
    statusCode: 200,
    body: JSON.stringify({ received: true }),
  };
}
