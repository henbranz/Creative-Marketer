INSERT INTO identity.tenants (id,name,slug,status)
VALUES ('00000000-0000-0000-0000-000000000014','Research downgrade','research-downgrade','active');
INSERT INTO identity.users (id,email,normalized_email,status)
VALUES ('00000000-0000-0000-0000-000000000014','research-downgrade@example.test','research-downgrade@example.test','active');
INSERT INTO catalog.brands (id,tenant_id,name,slug,status,created_by)
VALUES ('00000000-0000-0000-0000-000000000014','00000000-0000-0000-0000-000000000014','Research downgrade','research-downgrade','active','00000000-0000-0000-0000-000000000014');
INSERT INTO catalog.products (id,tenant_id,brand_id,name,slug,category,short_description,status,created_by)
VALUES ('00000000-0000-0000-0000-000000000014','00000000-0000-0000-0000-000000000014','00000000-0000-0000-0000-000000000014','Research product','research-product','Test','','draft','00000000-0000-0000-0000-000000000014');
INSERT INTO research.sources (id,tenant_id,product_id,source_type,canonical_url,display_name,category,status,created_by)
VALUES ('00000000-0000-0000-0000-000000000014','00000000-0000-0000-0000-000000000014','00000000-0000-0000-0000-000000000014','web_page','https://example.test/','Downgrade proof','other','active','00000000-0000-0000-0000-000000000014');
