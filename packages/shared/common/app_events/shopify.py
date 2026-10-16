"""
Output parameter schemas for Shopify APP trigger events.

Keys use dot-notation (e.g. "data.customer.email") so the expression picker
generates the correct {{ $nodeId.output.data.customer.email }} expressions,
which the expression resolver traverses as nested dict paths.

Schemas reflect the normalized IntegrationEventEnvelope.data shape, not the raw
Shopify payload — see shopify-normalizer.ts for the exact normalization logic.
"""

_PIXEL_BASE = [
    {"key": "data.sessionId",   "type": "string", "description": "Widget visitor session ID (null if anonymous at fire time)"},
    {"key": "data.clientId",    "type": "string", "description": "Shopify _shopify_y visitor ID"},
    {"key": "data.shopDomain",  "type": "string", "description": "Shopify store domain"},
    {"key": "data.eventId",     "type": "string", "description": "Pixel event ID (idempotency reference)"},
    {"key": "data.occurredAt",  "type": "string", "description": "ISO 8601 timestamp of the event"},
]

_PRODUCT_BLOCK = [
    {"key": "data.product.id",          "type": "string", "description": "Shopify product ID (numeric)"},
    {"key": "data.product.variantId",   "type": "string", "description": "Shopify variant ID (numeric)"},
    {"key": "data.product.title",       "type": "string", "description": "Product title"},
    {"key": "data.product.price",       "type": "number", "description": "Variant price"},
    {"key": "data.product.currencyCode","type": "string", "description": "ISO 4217 currency code"},
    {"key": "data.product.imageUrl",    "type": "string", "description": "Product image URL"},
    {"key": "data.product.url",         "type": "string", "description": "Storefront product URL"},
]

_ORDER_SCHEMA = [
    {"key": "data.customer.id",        "type": "string", "description": "Shopify customer ID"},
    {"key": "data.customer.email",     "type": "string", "description": "Customer email address"},
    {"key": "data.customer.phone",     "type": "string", "description": "Customer phone number"},
    {"key": "data.customer.firstName", "type": "string", "description": "Customer first name"},
    {"key": "data.customer.lastName",  "type": "string", "description": "Customer last name"},
    {"key": "data.order.id",           "type": "string", "description": "Shopify order ID"},
    {"key": "data.order.number",       "type": "number", "description": "Order number shown in admin"},
    {"key": "data.order.total",        "type": "number", "description": "Total order amount"},
    {"key": "data.order.currency",     "type": "string", "description": "ISO 4217 currency code"},
    {"key": "data.order.items",        "type": "array",
     "description": "Line items. Each: { id, title, quantity, price }"},
]

_CART_SCHEMA = [
    {"key": "data.email",              "type": "string", "description": "Customer email address"},
    {"key": "data.cart.cartUrl",       "type": "string", "description": "Direct URL to the cart"},
    {"key": "data.cart.totalPrice",    "type": "number", "description": "Cart total price"},
    {"key": "data.cart.currency",      "type": "string", "description": "ISO 4217 currency code"},
    {"key": "data.cart.items",         "type": "array",
     "description": "Cart items. Each: { title, variantTitle, quantity, price, image, url }"},
]

SHOPIFY_EVENT_SCHEMAS: dict[str, list[dict]] = {
    "order.created":   _ORDER_SCHEMA,
    "order.paid":      _ORDER_SCHEMA,
    "order.fulfilled": _ORDER_SCHEMA,
    "order.cancelled": _ORDER_SCHEMA,

    "customer.created": [
        {"key": "data.customer.id",             "type": "string",  "description": "Shopify customer ID"},
        {"key": "data.customer.email",          "type": "string",  "description": "Customer email address"},
        {"key": "data.customer.phone",          "type": "string",  "description": "Customer phone number"},
        {"key": "data.customer.firstName",      "type": "string",  "description": "Customer first name"},
        {"key": "data.customer.lastName",       "type": "string",  "description": "Customer last name"},
        {"key": "data.customer.createdAt",      "type": "string",  "description": "ISO 8601 timestamp when the customer was created"},
        {"key": "data.customer.tags",           "type": "string",  "description": "Comma-separated customer tags"},
        {"key": "data.customer.ordersCount",    "type": "number",  "description": "Total number of orders placed by this customer"},
        {"key": "data.customer.totalSpent",     "type": "string",  "description": "Total amount spent by the customer (as a string)"},
        {"key": "data.customer.verifiedEmail",  "type": "boolean", "description": "Whether the customer's email address is verified"},
        {"key": "data.customer.currency",       "type": "string",  "description": "Customer's default currency (ISO 4217)"},
        {"key": "data.customer.note",           "type": "string",  "description": "Internal admin note on the customer"},
        {"key": "data.customer.acceptsMarketing","type": "boolean","description": "Whether the customer has opted in to email marketing"},
        {"key": "data.customer.defaultAddress.address1",   "type": "string", "description": "Street address line 1"},
        {"key": "data.customer.defaultAddress.city",       "type": "string", "description": "City"},
        {"key": "data.customer.defaultAddress.province",   "type": "string", "description": "State / province"},
        {"key": "data.customer.defaultAddress.country",    "type": "string", "description": "Country name"},
        {"key": "data.customer.defaultAddress.zip",        "type": "string", "description": "Postal / ZIP code"},
        {"key": "data.customer.defaultAddress.countryCode","type": "string", "description": "ISO 3166-1 alpha-2 country code"},
    ],

    "cart.active":    _CART_SCHEMA,
    "cart.abandoned": _CART_SCHEMA,

    "refund.created": [
        {"key": "data.refund.id",     "type": "string", "description": "Shopify refund ID"},
        {"key": "data.refund.amount", "type": "number", "description": "Total refunded amount"},
    ],

    "product.added_to_cart": [
        *_PIXEL_BASE,
        {"key": "data.product.id",          "type": "string", "description": "Shopify product ID (numeric)"},
        {"key": "data.product.variantId",   "type": "string", "description": "Shopify variant ID (numeric)"},
        {"key": "data.product.quantity",    "type": "number", "description": "Quantity added"},
        {"key": "data.product.title",       "type": "string", "description": "Product title"},
        {"key": "data.product.variantTitle","type": "string", "description": "Variant title (e.g. 'Large / Blue')"},
        {"key": "data.product.price",       "type": "number", "description": "Variant price"},
        {"key": "data.product.currencyCode","type": "string", "description": "ISO 4217 currency code"},
        {"key": "data.product.imageUrl",    "type": "string", "description": "Product image URL"},
        {"key": "data.product.url",         "type": "string", "description": "Storefront product URL"},
    ],

    "cart.viewed": [
        *_PIXEL_BASE,
        {"key": "data.cartId",       "type": "string", "description": "Cart token"},
        {"key": "data.totalAmount",  "type": "number", "description": "Cart total price"},
        {"key": "data.currencyCode", "type": "string", "description": "ISO 4217 currency code"},
        {"key": "data.itemCount",    "type": "number", "description": "Number of line items in the cart"},
    ],

    "search.submitted": [
        *_PIXEL_BASE,
        {"key": "data.query",           "type": "string", "description": "Search query string"},
        {"key": "data.resultCount",     "type": "number", "description": "Number of results returned (0 = zero-result search)"},
        {"key": "data.productVariants", "type": "array",  "description": "Matched variants. Each: { productId, variantId, productTitle, variantTitle, price, currencyCode, imageUrl, url }"},
    ],

    "product.viewed": [
        *_PIXEL_BASE,
        *_PRODUCT_BLOCK,
    ],

    "collection.viewed": [
        *_PIXEL_BASE,
        {"key": "data.collection.id",     "type": "string", "description": "Shopify collection ID (numeric)"},
        {"key": "data.collection.title",  "type": "string", "description": "Collection title"},
        {"key": "data.productVariants",   "type": "array",  "description": "Variants in the collection. Each: { productId, variantId, productTitle, variantTitle, price, currencyCode, imageUrl, url }"},
    ],

    # Output schema for the fetch_session_events connector tool.
    # Key prefix is the tool name so the expression picker scopes it correctly.
    "tool.fetch_session_events": [
        {"key": "product_viewed.count",   "type": "number",  "description": "Number of product_viewed events in the window"},
        {"key": "product_viewed.items",   "type": "array",   "description": "product_viewed events. Each: { productId, variantId, productTitle, price, currencyCode, imageUrl, url }"},
        {"key": "collection_viewed.count","type": "number",  "description": "Number of collection_viewed events in the window"},
        {"key": "collection_viewed.items","type": "array",   "description": "collection_viewed events. Each: { collectionId, collectionTitle, productVariants }"},
        {"key": "search_submitted.count", "type": "number",  "description": "Number of search_submitted events in the window"},
        {"key": "search_submitted.items", "type": "array",   "description": "search_submitted events. Each: { searchQuery, searchResultCount, productVariants }"},
        {"key": "product_added_to_cart.count", "type": "number", "description": "Number of add-to-cart events in the window"},
        {"key": "product_added_to_cart.items", "type": "array",  "description": "add-to-cart events. Each: { productId, variantId, productTitle, quantity, price, currencyCode }"},
    ],

    "checkout.created": [
        {"key": "data.customer.id",        "type": "string"},
        {"key": "data.customer.email",     "type": "string"},
        {"key": "data.customer.phone",     "type": "string"},
        {"key": "data.customer.firstName", "type": "string"},
        {"key": "data.customer.lastName",  "type": "string"},
        {"key": "data.cart.id",            "type": "string", "description": "Checkout token"},
        {"key": "data.cart.recoverUrl",    "type": "string", "description": "Abandoned checkout recovery URL"},
        {"key": "data.cart.abandonedAt",   "type": "string", "description": "ISO-8601 timestamp of abandonment"},
        {"key": "data.cart.items",         "type": "array",
         "description": "Cart items. Each: { id, title, quantity, price }"},
        {"key": "data.sessionId",          "type": "string", "description": "Widget visitor session ID"},
        {"key": "data.customerId",         "type": "string", "description": "Internal customer record ID"},
    ],
}
