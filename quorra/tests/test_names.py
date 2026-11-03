from qindex.names import norm_url, split_words


def test_split_camel_snake_namespace():
    assert split_words("calculateFeaturedofferPrice") == ["calculate", "featuredoffer", "price"]
    assert split_words("MegaPromotionNewSkuRuleSkip") == ["mega", "promotion", "new", "sku", "rule", "skip"]
    assert split_words("SELLER_INVENTORY_CATEGORIES") == ["seller", "inventory", "categories"]
    assert split_words("Acme\\Framework\\Entity::getId") == ["acme", "framework", "entity", "get", "id"]
    assert split_words("use-list-params") == ["use", "list", "params"]
    assert split_words("HTTPServer2Client") == ["http", "server", "2", "client"]


def test_split_keeps_persian():
    assert split_words("عدم امکان") == ["عدم", "امکان"]


def test_norm_url():
    assert norm_url("/api/v2/pricing/vouchers/{voucher_id}") == "/pricing/vouchers/{}"
    assert norm_url("https://seller.example.com/api/v1/export/pdf/") == "/export/pdf"
    assert norm_url("/pricing/vouchers/${id}/report") == "/pricing/vouchers/{}/report"
    assert norm_url("pricing/repricing/types/:id/detail") == "/pricing/repricing/types/{}/detail"
    assert norm_url("/Pricing//Vouchers/") == "/pricing/vouchers"
    assert norm_url("/api/v1/pricing/vouchers?page=${p}") == "/pricing/vouchers"
    assert norm_url("/a/b#x") == "/a/b"
