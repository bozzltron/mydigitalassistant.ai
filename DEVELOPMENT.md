# MyDigitalAssistant.ai - Development Setup

## Services Available

1. **Backend API** - http://localhost:8000 (Healthy)
2. **Production Interface** - https://localhost:8443 (via Caddy proxy)  
3. **Development Server** - http://localhost:3000 (SolidJS/Vite)

## How to Use the Development Environment

### For Backend/API Development
```bash
# All services are ready:
docker compose ps  

# Access backend directly:
curl http://localhost:8000/health
```

### For Frontend Development 
The SolidJS development server runs on port 3000 but requires a different approach for integration:

1. **Direct access**: Visit `http://localhost:3000` to see the Vite dev server directly
2. **Backend interaction**: The frontend needs to be configured to talk to the backend API at http://assistant-backend:8000 when inside Docker network

### Caddy Configuration
Caddy proxies all requests to:
- `https://localhost/health` → `http://assistant-backend:8000/health`  
- `https://localhost/api/*` → `http://assistant-backend:8000/api/*`

For the SolidJS development workflow, you can use:
1. Direct access to Vite dev server at http://localhost:3000 
2. Or integrate with Docker network using internal service names

## Access Points

- Backend API: http://localhost:8000  
- Caddy Proxy: https://localhost:8443
- SolidJS Dev: http://localhost:3000  

## Next Steps

For a complete development workflow:
1. Run `docker compose up -d`
2. Access UI directly at http://localhost:3000 for hot-reloading
3. Backend functionality is available at http://localhost:8000
4. Production interface at https://localhost:8443

All services are working properly in their respective containers.