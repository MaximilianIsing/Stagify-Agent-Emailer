# API Endpoints

This document describes all available endpoints for the Stagify.ai Emailer Server.

## Endpoints

### GET /health

Health check endpoint to verify the server is running and responsive.

**Response:**
```json
{
  "status": "ok"
}
```

**Status Codes:**
- `200` - Server is healthy

---

### POST /extract-listing

Extract real estate listing information from a Compass.com agent page.

**Request Headers:**
- `Content-Type: application/json`

**Request Body:**
```json
{
  "agentName": "string",
  "endpointkey": "string"
}
```

**Parameters:**
- `agentName` (string, required) - The agent's name. Can be provided in any format (e.g., "Melody Acevedo" or "melody-acevedo"). The server automatically formats it to URL format (lowercase with hyphens).
- `endpointkey` (string, required) - Authentication key to validate the request

**Success Response:**
```json
{
  "address": "string",
  "daysOnMarket": "string",
  "firstRoomImage": "string|null"
}
```

**Response Fields:**
- `address` (string) - The property address extracted from the listing page
- `daysOnMarket` (string) - Number of days the property has been on the market
- `firstRoomImage` (string|null) - URL of the first image identified as "just a room" (bedroom, living room, kitchen, etc.) without people or external views. Returns `null` if no room image is found in the first 5 images.

**Error Responses:**

**400 Bad Request:**
```json
{
  "error": "Agent name is required"
}
```

**401 Unauthorized:**
```json
{
  "error": "Invalid endpoint key"
}
```

**500 Internal Server Error:**
```json
{
  "error": "Failed to extract listing data",
  "message": "Detailed error message"
}
```

**Status Codes:**
- `200` - Successfully extracted listing data
- `400` - Bad request (missing or invalid parameters)
- `401` - Unauthorized (invalid endpoint key)
- `500` - Internal server error

---

## How It Works

1. **Authentication**: The server validates the `endpointkey` against the stored key

2. **Name Formatting**: Automatically converts the agent name to URL format (lowercase, hyphens). For example, "Melody Acevedo" becomes "melody-acevedo".

3. **Navigation**: Uses Puppeteer to navigate to the Compass.com agent page

4. **Listing Selection**: Automatically finds and clicks on the first listing in the listings section

5. **Data Extraction**: 
   - Extracts the property address from the listing detail page
   - Extracts the days on market from the property details table
   - Iterates through the first 5 images on the listing page

6. **Image Analysis**: For each image (1-5), uses GPT-4 Vision API to determine if it shows "just a room" (interior room without people or external views). Stops at the first room image found.

7. **Response**: Returns all extracted data in JSON format

---

## Notes

- The server uses headless browser automation (Puppeteer) to interact with Compass.com
- Image analysis uses OpenAI's GPT-4 Vision API to identify room images
- Processing time may vary depending on page load times and image analysis
- The server checks up to 5 images; if no room image is found, `firstRoomImage` will be `null`
- Agent names can be provided in any format - the server automatically formats them for the URL

