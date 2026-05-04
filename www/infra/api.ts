import { bucket } from "./storage";
import { ordersTable } from "./database";

export const api = {
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
