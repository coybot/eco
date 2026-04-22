// DynamoDB table for orders
export const database = {
  ordersTable: new sst.aws.Dynamo("OrdersTable", {
    fields: {
      pk: "string", // ORDER#<orderId>
      sk: "string", // METADATA or ITEM#<itemId>
      gsi1pk: "string", // CUSTOMER#<email>
      gsi1sk: "string", // ORDER#<orderId>
    },
    primaryIndex: {
      hashKey: "pk",
      rangeKey: "sk",
    },
    globalIndexes: {
      // Index to query orders by customer email
      customerIndex: {
        hashKey: "gsi1pk",
        rangeKey: "gsi1sk",
      },
    },
    transform: {
      table: {
        billingMode: "PAY_PER_REQUEST", // On-demand pricing
        pointInTimeRecovery: {
          enabled: true,
        },
      },
    },
  }),
};

// Export for use in other stacks
export const { ordersTable } = database;
