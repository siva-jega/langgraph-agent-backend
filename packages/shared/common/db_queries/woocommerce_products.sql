SELECT
    p.id,
    p.name,
    p.slug,
    p.permalink,
    p.price::text AS price,
    p."stockStatus",
    p.purchasable,
    p.images,
    COALESCE(
        json_agg(
            json_build_object(
                'id', v.id,
                'name', v.name,
                'permalink', v.permalink,
                'regularPrice', v."regularPrice"::text,
                'salePrice', v."salePrice"::text,
                'onSale', v."onSale",
                'stockStatus', v."stockStatus",
                'stockQuantity', v."stockQuantity",
                'purchasable', v.purchasable,
                'attributes', v.attributes,
                'image', v.image
            )
        ) FILTER (WHERE v.id IS NOT NULL),
        '[]'::json
    ) AS variants
FROM "WooCommerceProduct" p
LEFT JOIN "WooCommerceProductVariant" v ON v."productId" = p.id
WHERE p.id = ANY($1::text[])
GROUP BY p.id;
