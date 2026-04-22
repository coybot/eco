import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { SESClient, SendEmailCommand } from "@aws-sdk/client-ses";

// SST Resource type (generated at deploy time)
declare const Resource: {
  OrdersTable: { name: string };
  AssetsBucket: { name: string };
};

const dynamoClient = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(dynamoClient);
const sesClient = new SESClient({ region: "us-east-1" });

// Helper to get table name
const getTableName = () => typeof Resource !== "undefined"
  ? Resource.OrdersTable.name
  : process.env.ORDERS_TABLE_NAME!;

interface OrderEvent {
  orderId: string;
  action: "send_confirmation" | "update_status" | "send_shipping_notification";
  newStatus?: string;
  trackingNumber?: string;
}

export async function handler(event: OrderEvent) {
  const { orderId, action } = event;

  // Get order from DynamoDB
  const orderResult = await docClient.send(new GetCommand({
    TableName: getTableName(),
    Key: {
      pk: `ORDER#${orderId}`,
      sk: "METADATA",
    },
  }));

  const order = orderResult.Item;

  if (!order) {
    throw new Error(`Order not found: ${orderId}`);
  }

  switch (action) {
    case "send_confirmation": {
      await sendOrderConfirmationEmail(order);
      break;
    }

    case "update_status": {
      if (!event.newStatus) {
        throw new Error("newStatus is required for update_status action");
      }
      await updateOrderStatus(orderId, event.newStatus);
      break;
    }

    case "send_shipping_notification": {
      if (!event.trackingNumber) {
        throw new Error("trackingNumber is required for send_shipping_notification action");
      }
      await sendShippingNotificationEmail(order, event.trackingNumber);
      break;
    }

    default:
      throw new Error(`Unknown action: ${action}`);
  }

  return { success: true, orderId, action };
}

async function sendOrderConfirmationEmail(order: Record<string, unknown>) {
  const customerEmail = order.customerEmail as string;
  const customerName = order.customerName as string;
  const orderId = order.orderId as string;
  const amountTotal = (order.amountTotal as number) / 100; // Convert from cents

  await sesClient.send(new SendEmailCommand({
    Source: "orders@astral.us",
    Destination: {
      ToAddresses: [customerEmail],
    },
    Message: {
      Subject: {
        Data: `Order Confirmed - ${orderId}`,
        Charset: "UTF-8",
      },
      Body: {
        Html: {
          Data: `
            <html>
              <body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #06b6d4;">Order Confirmed!</h1>
                <p>Hi ${customerName},</p>
                <p>Thank you for your order. We've received your payment and are preparing your shipment.</p>
                <p><strong>Order ID:</strong> ${orderId}</p>
                <p><strong>Total:</strong> $${amountTotal.toFixed(2)}</p>
                <p>You'll receive a shipping notification with tracking information once your order ships.</p>
                <p>Questions? Reply to this email or visit <a href="https://astral.us/contact">astral.us/contact</a>.</p>
                <p>Thanks,<br>The Astral Team</p>
              </body>
            </html>
          `,
          Charset: "UTF-8",
        },
        Text: {
          Data: `
Order Confirmed!

Hi ${customerName},

Thank you for your order. We've received your payment and are preparing your shipment.

Order ID: ${orderId}
Total: $${amountTotal.toFixed(2)}

You'll receive a shipping notification with tracking information once your order ships.

Thanks,
The Astral Team
          `,
          Charset: "UTF-8",
        },
      },
    },
  }));

  console.log(`Confirmation email sent to ${customerEmail} for order ${orderId}`);
}

async function updateOrderStatus(orderId: string, newStatus: string) {
  await docClient.send(new UpdateCommand({
    TableName: getTableName(),
    Key: {
      pk: `ORDER#${orderId}`,
      sk: "METADATA",
    },
    UpdateExpression: "SET #status = :status, updatedAt = :updatedAt",
    ExpressionAttributeNames: {
      "#status": "status",
    },
    ExpressionAttributeValues: {
      ":status": newStatus,
      ":updatedAt": new Date().toISOString(),
    },
  }));

  console.log(`Order ${orderId} status updated to ${newStatus}`);
}

async function sendShippingNotificationEmail(order: Record<string, unknown>, trackingNumber: string) {
  const customerEmail = order.customerEmail as string;
  const customerName = order.customerName as string;
  const orderId = order.orderId as string;

  await sesClient.send(new SendEmailCommand({
    Source: "orders@astral.us",
    Destination: {
      ToAddresses: [customerEmail],
    },
    Message: {
      Subject: {
        Data: `Your Order Has Shipped - ${orderId}`,
        Charset: "UTF-8",
      },
      Body: {
        Html: {
          Data: `
            <html>
              <body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #06b6d4;">Your Order Has Shipped!</h1>
                <p>Hi ${customerName},</p>
                <p>Great news! Your order is on its way.</p>
                <p><strong>Order ID:</strong> ${orderId}</p>
                <p><strong>Tracking Number:</strong> ${trackingNumber}</p>
                <p>Track your package at <a href="https://www.fedex.com/tracking?tracknumbers=${trackingNumber}">FedEx Tracking</a></p>
                <p>Thanks,<br>The Astral Team</p>
              </body>
            </html>
          `,
          Charset: "UTF-8",
        },
        Text: {
          Data: `
Your Order Has Shipped!

Hi ${customerName},

Great news! Your order is on its way.

Order ID: ${orderId}
Tracking Number: ${trackingNumber}

Track your package at: https://www.fedex.com/tracking?tracknumbers=${trackingNumber}

Thanks,
The Astral Team
          `,
          Charset: "UTF-8",
        },
      },
    },
  }));

  // Update order status
  await updateOrderStatus(orderId, "shipped");

  console.log(`Shipping notification sent to ${customerEmail} for order ${orderId}`);
}
