# EstateAI — AI Real Estate Sales Platform

## Included
- Customer website with property search/filter
- Public AI chatbot grounded in published property + public business data
- Owner-only login with secure HttpOnly session cookie
- Remember-this-device option (session token; password is never stored in browser)
- Owner property CRUD, publish/unpublish, image upload
- Groq vision analysis of uploaded property images
- Lead/enquiry capture and private owner lead list
- Site-visit request flow
- Owner settings and AI greeting
- Private AI conversation history
- Basic analytics and audit log
- Vercel FastAPI entrypoint/config

## Vercel Environment Variables
- `GROQ_API_KEY` — your Groq key
- `GROQ_TEXT_MODEL` — default `openai/gpt-oss-120b`
- `GROQ_VISION_MODEL` — default `qwen/qwen3.8-27b`
- `JWT_SECRET` — long random secret
- `OWNER_USERNAME` — owner login username
- `OWNER_PASSWORD` — owner login password
- `SESSION_DAYS` — default 30 for remembered login
- `MAX_UPLOAD_MB` — default 10

## Important production note
The included JSON store is a simple zero-setup fallback. Vercel serverless storage is ephemeral, so permanent production data requires an external database/object-storage layer (for example PostgreSQL + object storage). Do not treat the JSON fallback as durable production storage.

## Customer accounts
No customer login is required for browsing/search/AI. When a customer submits an enquiry or visit request, name + phone are required and consent is captured. This avoids abandoned account creation while still giving the owner a usable lead record.

## AI image rule
The vision model is instructed to report only visually supported observations. It must not invent exact price, location, owner, area or legal facts. Groq vision currently supports image input; this project uses the documented multimodal chat-completions pattern.
