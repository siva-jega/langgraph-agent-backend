from app.modules.channels.ecommerce.shopify import ShopifyProvider


def test_extract_products_from_kb_backed_shopify_source() -> None:
    provider = ShopifyProvider()
    products = provider.extract_products_from_sources(
        [
            {"name": "Knowledge Base", "tool": "Search", "data": []},
            {
                "name": "Shopify",
                "tool": "Get Products",
                "data": [
                    {
                        "id": "gid://shopify/Product/123",
                        "name": "Idika Handmade Diary",
                        "product_url": "https://idikagoa.in/products/idika-handmade-diary",
                        "price_range": "INR 270.00",
                        "image_url": "https://cdn.shopify.com/diary.png",
                    }
                ],
            },
        ]
    )

    assert products == [
        {
            "id": "gid://shopify/Product/123",
            "name": "Idika Handmade Diary",
            "product_url": "https://idikagoa.in/products/idika-handmade-diary",
            "price_range": "INR 270.00",
            "image_url": "https://cdn.shopify.com/diary.png",
        }
    ]
