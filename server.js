const express = require('express');
const puppeteer = require('puppeteer');
const OpenAI = require('openai');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(express.json());

// Read keys from files or environment variables (env vars take precedence for deployment)
function readKeyFromFileOrEnv(envVar, filePath) {
  if (process.env[envVar]) {
    return process.env[envVar];
  }
  try {
    return fs.readFileSync(path.join(__dirname, filePath), 'utf8').trim();
  } catch (error) {
    throw new Error(`Missing ${envVar} environment variable and ${filePath} file not found`);
  }
}

const ENDPOINT_KEY = readKeyFromFileOrEnv('ENDPOINT_KEY', 'endpointkey.txt');
const GPT_API_KEY = readKeyFromFileOrEnv('GPT_API_KEY', 'gpt-key.txt');

// Read debug setting from env var or file
let DEBUG = false;
if (process.env.DEBUG) {
  DEBUG = process.env.DEBUG.toLowerCase() === 'true';
} else {
  try {
    DEBUG = fs.readFileSync(path.join(__dirname, 'debug.txt'), 'utf8').trim().toLowerCase() === 'true';
  } catch (error) {
    // Default to false if debug.txt doesn't exist
    DEBUG = false;
  }
}

// Debug logging function
function debugLog(...args) {
  if (DEBUG) {
    console.log(...args);
  }
}

function debugWarn(...args) {
  if (DEBUG) {
    console.warn(...args);
  }
}

function debugError(...args) {
  if (DEBUG) {
    console.error(...args);
  }
}

// Initialize OpenAI
const openai = new OpenAI({
  apiKey: GPT_API_KEY
});

// Function to format agent name to URL format (lowercase, hyphens)
function formatAgentNameForUrl(agentName) {
  return agentName
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '-')  // Replace spaces with hyphens
    .replace(/[^a-z0-9-]/g, '')  // Remove special characters
    .replace(/-+/g, '-')  // Replace multiple hyphens with single hyphen
    .replace(/^-|-$/g, '');  // Remove leading/trailing hyphens
}

// Function to check if image is "just a room" using GPT Vision API
async function isRoomImage(imageUrl) {
  try {
    const response = await openai.chat.completions.create({
      model: "gpt-4o",
      messages: [
        {
          role: "user",
          content: [
            {
              type: "text",
              text: "Is this image showing just a room (like a bedroom, living room, kitchen, etc.) without people or external views? Respond with only 'yes' or 'no'."
            },
            {
              type: "image_url",
              image_url: {
                url: imageUrl
              }
            }
          ]
        }
      ],
      max_tokens: 10
    });

    const answer = response.choices[0].message.content.toLowerCase().trim();
    return answer.includes('yes');
  } catch (error) {
    debugError('Error checking image with GPT:', error);
    return false;
  }
}

// Main endpoint
app.post('/extract-listing', async (req, res) => {
  try {
    const { agentName, endpointkey } = req.body;

    // Validate endpoint key
    if (endpointkey !== ENDPOINT_KEY) {
      return res.status(401).json({ error: 'Invalid endpoint key' });
    }

    if (!agentName) {
      return res.status(400).json({ error: 'Agent name is required' });
    }

    // Format agent name to URL format (e.g., "Melody Acevedo" -> "melody-acevedo")
    const formattedAgentName = formatAgentNameForUrl(agentName);
    
    // Construct agent URL
    const agentUrl = `https://www.compass.com/agents/${formattedAgentName}/`;

    debugLog(`Starting extraction for agent: ${agentName} (formatted: ${formattedAgentName})`);

    // Launch browser
    const browser = await puppeteer.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--no-first-run',
        '--no-zygote',
        '--single-process',
        '--disable-gpu'
      ]
    });

    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080 });

    try {
      // Navigate to agent page
      debugLog('Navigating to agent page...');
      await page.goto(agentUrl, { waitUntil: 'networkidle2', timeout: 30000 });

      // Wait for listings section and find first listing
      debugLog('Looking for first listing...');
      // Wait a bit for page to load
      await page.waitForTimeout(3000);
      
      // Try to find first listing element in the listings section
      // XPath: /html/body/main/div/div/section[2]/div - get first element
      let firstListing = await page.$x('/html/body/main/div/div/section[2]/div[1]');
      if (firstListing.length === 0) {
        // Wait a bit more and try again
        await page.waitForTimeout(3000);
        firstListing = await page.$x('/html/body/main/div/div/section[2]/div[1]');
      }
      
      if (firstListing.length === 0) {
        throw new Error('Listings section not found on page');
      }

      debugLog('Clicking on first listing...');
      
      // Scroll element into view and wait for it to be visible
      await page.evaluate((el) => {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, firstListing[0]);
      await page.waitForTimeout(1000);
      
      // Try to find a clickable link within the listing element
      const href = await page.evaluate((el) => {
        // Look for an anchor tag first
        const link = el.querySelector('a');
        if (link && link.href) {
          return link.href;
        }
        // Check if the element itself is a link
        if (el.tagName === 'A' && el.href) {
          return el.href;
        }
        // Check if element has onclick or data attributes that might contain URL
        if (el.onclick || el.getAttribute('data-href')) {
          return el.getAttribute('data-href') || el.getAttribute('href');
        }
        return null;
      }, firstListing[0]);
      
      // Try multiple click methods
      if (href) {
        debugLog(`Found link, navigating directly to: ${href}`);
        await page.goto(href, { waitUntil: 'networkidle2', timeout: 30000 });
      } else {
        debugLog('No direct link found, attempting to click element...');
        try {
          // Method 1: Try clicking the element directly with navigation wait
          await Promise.all([
            firstListing[0].click(),
            page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 })
          ]);
          debugLog('Successfully clicked and navigated');
        } catch (error) {
          debugLog('Direct click failed, trying JavaScript click...');
          try {
            // Method 2: Click using JavaScript in browser context
            await page.evaluate((el) => {
              // Try to find and click a link
              const link = el.querySelector('a');
              if (link) {
                link.click();
              } else {
                // Trigger click event on the element
                const clickEvent = new MouseEvent('click', {
                  view: window,
                  bubbles: true,
                  cancelable: true
                });
                el.dispatchEvent(clickEvent);
                el.click();
              }
            }, firstListing[0]);
            await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 });
            debugLog('Successfully clicked using JavaScript method');
          } catch (error2) {
            throw new Error(`Failed to click listing: ${error2.message}`);
          }
        }
      }

      // Wait for listing page to fully load
      await page.waitForTimeout(2000);

      // Extract address
      debugLog('Extracting address...');
      let address = 'Address not found';
      try {
        // Wait for address element with retry
        let addressElement = await page.$x('/html/body/div[1]/main/div/main/div[1]/div[1]/div/div/h1/p');
        if (addressElement.length === 0) {
          await page.waitForTimeout(2000);
          addressElement = await page.$x('/html/body/div[1]/main/div/main/div[1]/div[1]/div/div/h1/p');
        }
        if (addressElement.length > 0) {
          address = await page.evaluate(el => el.textContent.trim(), addressElement[0]);
        }
      } catch (error) {
        debugWarn('Could not extract address:', error.message);
      }

      // Extract days on market
      debugLog('Extracting days on market...');
      let daysOnMarket = 'Days on market not found';
      try {
        // Wait for days on market element with retry
        let daysOnMarketElement = await page.$x('/html/body/div[1]/main/div/main/div[4]/div[2]/table/tbody/tr[3]/td');
        if (daysOnMarketElement.length === 0) {
          await page.waitForTimeout(2000);
          daysOnMarketElement = await page.$x('/html/body/div[1]/main/div/main/div[4]/div[2]/table/tbody/tr[3]/td');
        }
        if (daysOnMarketElement.length > 0) {
          daysOnMarket = await page.evaluate(el => el.textContent.trim(), daysOnMarketElement[0]);
        }
      } catch (error) {
        debugWarn('Could not extract days on market:', error.message);
      }

      // Find first room image
      debugLog('Looking for room images...');
      let firstRoomImage = null;

      for (let n = 1; n <= 5; n++) {
        try {
          const imageXPath = `/html/body/div[1]/main/div/main/div[3]/div[1]/div/div[1]/div[2]/div/div/div[${n}]/img`;
          const imageElements = await page.$x(imageXPath);
          
          if (imageElements.length > 0) {
            // Try to get image URL from src or data-src
            const imageUrl = await page.evaluate(el => {
              return el.src || el.getAttribute('data-src') || el.getAttribute('data-lazy-src') || '';
            }, imageElements[0]);

            if (imageUrl && imageUrl.startsWith('http')) {
              debugLog(`Checking image ${n}: ${imageUrl}`);

              // Check if this is a room image using GPT
              const isRoom = await isRoomImage(imageUrl);
              
              if (isRoom) {
                firstRoomImage = imageUrl;
                debugLog(`Found room image at index ${n}`);
                break;
              }
            } else {
              debugLog(`Image ${n} has invalid URL: ${imageUrl}`);
            }
          }
        } catch (error) {
          debugLog(`Image ${n} not found or error:`, error.message);
          // Continue to next image
        }
      }

      await browser.close();

      // Return results
      const result = {
        address: address,
        daysOnMarket: daysOnMarket,
        firstRoomImage: firstRoomImage || null
      };

      debugLog('Extraction complete:', result);
      res.json(result);

    } catch (error) {
      await browser.close();
      throw error;
    }

  } catch (error) {
    debugError('Error:', error);
    res.status(500).json({ 
      error: 'Failed to extract listing data', 
      message: error.message 
    });
  }
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  debugLog(`Server running on port ${PORT}`);
  debugLog(`Health check: http://localhost:${PORT}/health`);
});

