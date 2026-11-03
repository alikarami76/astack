# Featured Offer — Documentation Index

The featured offer (پیشنهاد ویژه) decides the default listing for a book. The pick is stored by `FeaturedOfferService` in `featured_offers`.
## How an offer is chosen
`FeaturedOfferScorer` scores offers; پیشنهاد ویژه برنده is recomputed by `RecalculateFeaturedCommand`.
