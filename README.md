# EstateAI — Premium AI Real Estate Sales Platform

A single-codebase real-estate website with a public customer experience and a private owner control center.

## Included

### Customer experience
- Premium responsive real-estate website
- Public property browsing and detail pages
- Search by natural text, location, property type, BHK and max price
- Customer signup/login
- Saved properties
- Customer activity: enquiries, site visits and chats
- Property enquiry flow with consent
- Site-visit booking flow
- Direct Call and WhatsApp buttons driven by owner settings
- Floating AI property advisor

### AI sales assistant
- Groq API integration via environment variable
- Natural-language property search and conversation
- Uses only published property data + public business settings
- Can answer general business questions when information is configured
- Can guide customers to enquiry, call, WhatsApp and site visit
- Stores the full website chatbot conversation for owner review
- Does not expose unpublished listings or private owner dashboard information
- Does not invent price, availability, location, legal facts or owner data

### Owner control center
- Separate owner login
- Dashboard overview
- Add/edit/delete properties
- Publish/unpublish properties
- Full property fields: purpose, type, price, BHK, bathrooms, areas, floors, address, locality, city, state, pincode, map URL, facing, furnishing, parking, construction year, RERA, amenities and description
- Multiple property image uploads
- Leads and enquiry pipeline
- Lead status management
- Customer account list
- Full private AI conversation history
- Site visit management + status
- Analytics: views, searches, calls, WhatsApp clicks, enquiries, visits and chats
- Owner settings for business name, public contact details, WhatsApp, call number, hours, address, branding and AI business context
- JSON data export
- Audit log in backend data

## Environment variables

```env
GROQ_API_KEY=
GROQ_TEXT_MODEL=openai/gpt-oss-120b
JWT_SECRET=replace-with-a-long-random-secret
OWNER_USERNAME=owner
OWNER_PASSWORD=replace-with-a-strong-unique-password
SESSION_DAYS=30
MAX_UPLOAD_MB=2
SECURE_COOKIE=true
COOKIE_SAMESITE=lax
ALLOWED_ORIGINS=
CLOUDINARY_URL=
```

GROQ_API_KEY is optional: without it, the app uses a local fallback assistant. JWT_SECRET and OWNER_PASSWORD are mandatory and must be strong unique values before deployment. If Cloudinary is not configured, images fall back to JSON/base64 storage for testing only.

## Local run

```bash
pip install -r requirements.txt
uvicorn backend.app:app --reload
```

Open the local URL shown by Uvicorn.

## Vercel

The repository includes `api/index.py` and `vercel.json` for a FastAPI deployment.

### Important production storage note
The included JSON store is intentionally zero-setup so the client can test and sell the product without connecting a database. Vercel/serverless storage is not a durable database. For a production client with real business traffic, replace the JSON persistence layer with PostgreSQL and move property images to durable object storage. The API boundaries are kept simple so this upgrade can be made without redesigning the UI.

## Security note
The demo uses signed HttpOnly cookies and server-side credentials from environment variables. Use a long random `JWT_SECRET`, change `OWNER_PASSWORD`, and serve over HTTPS. Customer passwords use Argon2 with automatic migration from the old SHA-256 format. Chat sessions are server-bound with an HttpOnly cookie. For serious production traffic, replace the JSON persistence layer with PostgreSQL/another durable database, use managed object storage such as Cloudinary, and add infrastructure-level rate limiting/WAF and monitoring.
