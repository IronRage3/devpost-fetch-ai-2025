from flask import Flask, request, render_template_string, session, redirect
import google.generativeai as genai
import requests
import logging
import re
import json
import time
# Add to your existing imports
import random  # For generating mock booking codes
import requests  # For Zomato API calls
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from uagents import Agent, Context

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = '77e48154dac6faee1c8847522388b0a447da9dcfa0563be9'  # Important for session security

# API Configuration
GOOGLE_API_KEY = "insert google api key here"
WEATHER_API_KEY = "insert weather api key here"
AMADEUS_API_KEY = "insert amadeus api key here"
AMADEUS_API_SECRET = "insert amadeus api secret key here"  # You need to add this
# ADD ZOMATO CONFIG HERE ▼
YELP_API_KEY = "insert yelp api key here"
genai.configure(api_key=GOOGLE_API_KEY)

# Fetch.ai wallet address (for hotel price alerts)
MY_WALLET_ADDRESS = "fetch162cu5xtcc7eqmt3fk7zdvhgqnhkhnly4lqwgy3"  # Replace with your actual Fetch.ai wallet address

# Assistant Settings
ASSISTANT_NAME = "GlobalMate"
SPEECH_ENABLED = True
VOICE_INPUT_ENABLED = True

# Response Templates
predefined_responses = {
    "greeting": f"🌍 Hi! I'm {ASSISTANT_NAME}. Ask me about hotels, weather or travel tips!",
    "hotel_search": "🏨 Here are hotels matching your criteria:",
    "weather": "⛅ Checking weather data...",
    "goodbye": "✈️ Safe travels!",
    "thanks": "😊 You're welcome!",
    "name": f"🤖 I'm {ASSISTANT_NAME}, your travel assistant",
    "search": "🔍 Search results:",
    "unclear": "🤔 Please specify more details",
    "error": "⚠️ Service unavailable",
    "default": "❓ Ask me about hotels, weather, or anything!",
    "change_name": "✏️ What should I call myself?"
}

# Initialize Amadeus API agent
# Add this to initialize the agent properly
agent = Agent(
    name="travel-assistant",
    port=8000,
    endpoint=["http://127.0.0.1:8000/submit"]
)


# Amadeus API functions
def get_amadeus_token():
    """Get the Amadeus API access token"""
    url = "https://test.api.amadeus.com/v1/security/oauth2/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    payload = {
        "grant_type": "client_credentials",
        "client_id": AMADEUS_API_KEY,
        "client_secret": AMADEUS_API_SECRET
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload)
        if response.status_code == 200:
            return response.json()["access_token"]
        else:
            logger.error(f"Amadeus token error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Amadeus token error: {e}")
        return None

def search_hotels_amadeus(city, check_in_date=None, check_out_date=None, adults=1, price_range=None):
    """Search hotels using Amadeus API"""
    token = get_amadeus_token()
    if not token:
        logger.error("Failed to get Amadeus token")
        return None
    
    # Get city code first
    city_code = get_city_code(city, token)
    if not city_code:
        logger.error(f"Failed to get city code for {city}")
        return None
    
    # Step 1: Get hotel IDs in the city
    url = 'https://test.api.amadeus.com/v1/reference-data/locations/hotels/by-city'
    headers = {'Authorization': f'Bearer {token}'}
    params = {
        'cityCode': city_code,
        'radius': 5,
        'radiusUnit': 'KM',
        'hotelSource': 'ALL'
    }
    
    logger.info(f"Searching hotels in city: {city_code}")
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        logger.error(f"Hotel search error: {response.status_code} - {response.text}")
        return None
    
    hotels_data = response.json()
    if not hotels_data or 'data' not in hotels_data:
        logger.error("No hotel data found")
        return None
    
    # Extract hotel IDs (limit to 5 to avoid too large requests)
    hotel_ids = [hotel['hotelId'] for hotel in hotels_data.get('data', [])[:5]]
    
    if not hotel_ids:
        logger.error(f"No hotels found in {city_code}")
        return None
    
    logger.info(f"Found {len(hotel_ids)} hotels in {city_code}")
    
    # Step 2: Get offers for these hotels
    url = 'https://test.api.amadeus.com/v3/shopping/hotel-offers'
    params = {
        'hotelIds': ','.join(hotel_ids),
        'checkInDate': check_in_date or (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
        'checkOutDate': check_out_date or (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d'),
        'adults': adults,
        'paymentPolicy': 'NONE',
        'bestRateOnly': 'true',
        'view': 'FULL',
        'sort': 'PRICE'
    }
    
    if price_range:
        params['priceRange'] = price_range
    
    logger.info(f"Getting hotel offers with params: {params}")
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        logger.error(f"Hotel offers error: {response.status_code} - {response.text}")
        return None
    
    return response.json()

def get_city_code(city_name, token):
    """Get the city code for a given city name using Amadeus API"""
    # Handle common cities directly
    city_mapping = {
        "new york": "NYC",
        "paris": "PAR",
        "london": "LON",
        # Add more common cities as needed
    }
    
    # Check if city is in our mapping
    normalized_city = city_name.lower().strip()
    if normalized_city in city_mapping:
        return city_mapping[normalized_city]
    
    # Otherwise use the API
    url = "https://test.api.amadeus.com/v1/reference-data/locations"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    params = {
        "keyword": city_name,
        "subType": "CITY"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            if data["data"] and len(data["data"]) > 0:
                return data["data"][0]["iataCode"]
        return None
    except Exception as e:
        logger.error(f"Amadeus city code error: {e}")
        return None

def process_amadeus_hotels(hotel_data):
    """Process Amadeus API hotel data into a standardized format"""
    hotels = []
    
    if not hotel_data or "data" not in hotel_data or not hotel_data["data"]:
        return []
    
    for hotel in hotel_data["data"]:
        try:
            # Get the first offer (best rate)
            offer = hotel['offers'][0] if hotel.get('offers') else {}
            price = offer.get('price', {})
            
            # Get image
            image_url = "https://via.placeholder.com/300x200?text=No+Image"
            if "media" in hotel['hotel'] and hotel['hotel']["media"]:
                for media in hotel['hotel']["media"]:
                    if media.get("category") == "EXTERIOR" and "uri" in media:
                        image_url = media["uri"]
                        break
            
            hotels.append({
                "name": hotel['hotel']['name'],
                "price": f"{price.get('total', 'N/A')} {price.get('currency', '')}",
                "rating": str(hotel['hotel'].get('rating', 'N/A')),
                "distance": f"{hotel['hotel'].get('distance', {}).get('value', '')} {hotel['hotel'].get('distance', {}).get('unit', '')}",
                "image": image_url,
                "link": "#",  # Placeholder link
                "hotel_id": hotel['hotel']['hotelId']
            })
        except Exception as e:
            logger.error(f"Error processing hotel: {e}")
            continue
    
    return hotels
def get_real_restaurants(location, cuisine=None):
    # 1. Get city ID
    cities_url = "https://api.yelp.com/v3/businesses/search"
    headers = {"user-key": ZOMATO_API_KEY}
    params = {"q": location}
    
    city_data = requests.get(cities_url, headers=headers, params=params).json()
    city_id = city_data["location_suggestions"][0]["id"] if city_data["location_suggestions"] else None
    
    if not city_id:
        return None
    
    # 2. Search restaurants
    search_url = "https://developers.zomato.com/api/v2.1/search"
    params = {
        "entity_id": city_id,
        "entity_type": "city",
        "cuisines": cuisine if cuisine else "",
        "count": 5  # Limit to 5 results
    }
    
    restaurants = requests.get(search_url, headers=headers, params=params).json()
    
    # 3. Format response
    formatted = []
    for r in restaurants.get("restaurants", []):
        restaurant = r["restaurant"]
        formatted.append({
            "id": restaurant["id"],
            "name": restaurant["name"],
            "cuisine": ", ".join(cuisine["cuisine_name"] for cuisine in restaurant.get("cuisines", "").split(",")[:2]),
            "price_range": restaurant["price_range"] * "$",  # Converts 2 to $$
            "rating": restaurant["user_rating"]["aggregate_rating"],
            "image": restaurant["featured_image"],
            "location": f"{restaurant['location']['locality']}, {restaurant['location']['city']}"
        })
    
    return formatted
# Extract location from query
def extract_location(text):
    """Extract location from any query format"""
    text = text.lower().strip()
    
    patterns = [
        r"(?:hotels?|stay|accommodation) in (.+?)(?:\?|$)",
        r"weather in (.+?)(?:\?|$)",
        r"(.+?) (?:hotels?|weather)"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            location = match.group(1).strip()
            return re.sub(r'[^\w\s-]', '', location).title()
    
    return None

def extract_hotel_preferences(text):
    """Extract budget and preferences from hotel queries"""
    text = text.lower()
    budget = None
    preferences = []
    
    # Extract budget
    budget_match = re.search(r'(\$|usd)?\s?(\d{2,4})\s?(dollars|usd|dollar)?\b', text)
    if budget_match:
        budget = int(budget_match.group(2))
        preferences.append("budget")
    
    # Extract preferences
    if any(word in text for word in ['street food', 'local food', 'food market']):
        preferences.append("street_food")
    if 'cheap' in text or 'budget' in text or 'affordable' in text:
        preferences.append("budget")
    if 'luxury' in text or '5 star' in text or 'high end' in text:
        preferences.append("luxury")
    if 'family' in text or 'kids' in text:
        preferences.append("family")
    
    return budget, preferences

def get_hotel_recommendations(location, budget=None, preferences=None):
    """Get hotel data using Amadeus API with fallbacks"""
    try:
        # Set default dates if not specified (tomorrow and day after)
        from datetime import datetime, timedelta
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        
        # Convert budget to Amadeus format if available
        price_range = None
        if budget:
            price_range = f"0-{budget}"
        
        # Get hotels from Amadeus
        hotels_data = search_hotels_amadeus(location, tomorrow, day_after, price_range=price_range)
        hotels = process_amadeus_hotels(hotels_data)
        
        if not hotels:
            # Fallback to encoded links
            encoded_location = requests.utils.quote(location.replace(' ', '+'))
            return {
                "hotels": [],
                "fallbacks": [
                    {
                        "name": f"Booking.com results for {location}",
                        "link": f"https://www.booking.com/searchresults.html?ss={encoded_location}"
                    },
                    {
                        "name": f"Hostelworld {location} hostels",
                        "link": f"https://www.hostelworld.com/search-results?location={encoded_location}"
                    }
                ]
            }
            
        return {"hotels": hotels, "fallbacks": []}
        
    except Exception as e:
        logger.error(f"Hotel search error: {e}")
        encoded_location = requests.utils.quote(location.replace(' ', '+'))
        
        return {
            "hotels": [],
            "fallbacks": [
                {
                    "name": f"Booking.com {location} hotels",
                    "link": f"https://www.booking.com/searchresults.html?ss={encoded_location}"
                },
                {
                    "name": f"Agoda {location} hotels",
                    "link": f"https://www.agoda.com/search?city={encoded_location}"
                }
            ]
        }
# 1. Add these functions after your existing Amadeus hotel functions

def search_flights_amadeus(origin, destination, departure_date, return_date=None, adults=1):
    """Search flights using Amadeus API"""
    token = get_amadeus_token()
    if not token:
        logger.error("Failed to get Amadeus token")
        return None
    
    # Build request parameters
    url = 'https://test.api.amadeus.com/v2/shopping/flight-offers'
    headers = {'Authorization': f'Bearer {token}'}
    
    params = {
        'originLocationCode': origin,
        'destinationLocationCode': destination,
        'departureDate': departure_date,
        'adults': adults,
        'max': 5,  # Limit results to 5 flights
        'currencyCode': 'USD'
    }
    
    # Add return date if provided (for round trip)
    if return_date:
        params['returnDate'] = return_date
    
    logger.info(f"Searching flights from {origin} to {destination} on {departure_date}")
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        logger.error(f"Flight search error: {response.status_code} - {response.text}")
        return None
    
    flights_data = response.json()
    if not flights_data or 'data' not in flights_data:
        logger.error("No flight data found")
        return None
    
    return flights_data

def process_amadeus_flights(flights_data):
    """Process Amadeus API flight data into a standardized format"""
    flights = []
    
    if not flights_data or "data" not in flights_data or not flights_data["data"]:
        return []
    
    # Get carrier information for airline codes
    carriers = {}
    if "dictionaries" in flights_data and "carriers" in flights_data["dictionaries"]:
        carriers = flights_data["dictionaries"]["carriers"]
    
    for flight in flights_data["data"]:
        try:
            # Get price info
            price = flight.get('price', {})
            total_price = price.get('total', 'N/A')
            currency = price.get('currency', 'USD')
            
            # Process itineraries (outbound and return if round trip)
            outbound = flight['itineraries'][0]
            
            # Get departure and arrival info
            first_segment = outbound['segments'][0]
            last_segment = outbound['segments'][-1]
            
            departure_time = first_segment['departure']['at']
            arrival_time = last_segment['arrival']['at']
            
            # Format times
            departure_dt = datetime.fromisoformat(departure_time.replace('Z', '+00:00'))
            arrival_dt = datetime.fromisoformat(arrival_time.replace('Z', '+00:00'))
            
            departure_formatted = departure_dt.strftime('%H:%M %d %b')
            arrival_formatted = arrival_dt.strftime('%H:%M %d %b')
            
            # Calculate duration
            duration_minutes = int(outbound.get('duration', 'PT0H').replace('PT', '').replace('H', '').replace('M', ''))
            hours, minutes = divmod(duration_minutes, 60)
            duration = f"{hours}h {minutes}m"
            
            # Get airline info for first segment
            airline_code = first_segment['carrierCode']
            airline_name = carriers.get(airline_code, airline_code)
            
            # Count stops
            stops = len(outbound['segments']) - 1
            stops_text = "Non-stop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
            
            # Format flight numbers
            flight_numbers = [f"{seg['carrierCode']}{seg['number']}" for seg in outbound['segments']]
            flight_number_text = " → ".join(flight_numbers)
            
            flights.append({
                "airline": airline_name,
                "flight_numbers": flight_number_text,
                "departure": departure_formatted,
                "arrival": arrival_formatted,
                "duration": duration,
                "stops": stops_text,
                "price": f"{total_price} {currency}",
                "booking_link": "#",  # Placeholder link
                "flight_id": flight['id']
            })
            
        except Exception as e:
            logger.error(f"Error processing flight: {e}")
            continue
    
    return flights
def get_weather(city):
    """Fetch comprehensive weather data for any location"""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=en"
        response = requests.get(url)
        data = response.json()
        
        if response.status_code == 200:
            return {
                "city": data['name'],
                "country": data['sys']['country'] if 'sys' in data else "",
                "temp": round(data['main']['temp'], 1),
                "feels_like": round(data['main']['feels_like'], 1),
                "conditions": data['weather'][0]['description'].capitalize(),
                "humidity": data['main']['humidity'],
                "wind": round(data['wind']['speed'], 1),
                "icon": data['weather'][0]['icon'],
                "forecast_link": f"https://www.accuweather.com/en/search-locations?query={city}"
            }
        return None
    except Exception as e:
        logger.error(f"Weather API error for {city}: {e}")
        return None

def format_weather(weather_data):
    """Format weather response with emoji visualization"""
    if not weather_data:
        return "⚠️ Couldn't retrieve weather data. Try another location."
    
    # Weather icon mapping
    icon_map = {
        '01': '☀️',  # clear sky
        '02': '⛅',  # few clouds
        '03': '☁️',  # scattered clouds
        '04': '☁️',  # broken clouds
        '09': '🌧️',  # shower rain
        '10': '🌦️',  # rain
        '11': '⚡',  # thunderstorm
        '13': '❄️',  # snow
        '50': '🌫️'   # mist
    }
    icon_code = weather_data['icon'][:2]
    weather_icon = icon_map.get(icon_code, '🌡️')
    
    location = f"{weather_data['city']}, {weather_data['country']}" if weather_data['country'] else weather_data['city']
    
    return f"""
    <div class="weather-card">
        {weather_icon} <b>{location} Weather</b>
        • Temperature: {weather_data['temp']}°C (Feels like {weather_data['feels_like']}°C)
        • Conditions: {weather_data['conditions']}
        • Humidity: {weather_data['humidity']}%
        • Wind: {weather_data['wind']} m/s

        <a href='{weather_data['forecast_link']}' target='_blank' class='forecast-link'>View detailed forecast →</a>
    </div>
    """

def detect_query_type(text):
    text = text.lower().strip()
    
    # Hotel detection
    if any(word in text for word in ['hotel', 'stay', 'accommodation', 'hostel']):
        location = extract_location(text)
        if location:
            budget, preferences = extract_hotel_preferences(text)
            return ("hotel", {"location": location, "budget": budget, "preferences": preferences})
        return "hotel_search"
    
    # Weather detection
    if any(w in text for w in ['weather', 'temperature', 'forecast']):
        location = extract_location(text)
        if location:
            return ("weather", location)
        return "weather"
    
    # Other intents
    greetings = ['hi', 'hello', 'hey']
    if any(text.startswith(g) for g in greetings):
        return "greeting"
    
    if any(g in text for g in ['bye', 'goodbye']):
        return "goodbye"
    
    if any(t in text for t in ['thank', 'thanks']):
        return "thanks"
    
    if any(n in text for n in ['your name', 'who are you']):
        return "name"
    
    if "change your name" in text:
        return "change_name"
    
    if any(s in text for s in ['search for', 'find info']):
        return "search"
    
    return None

def process_query(query_type, user_input):
    """Process user query and return formatted response"""
    if isinstance(query_type, tuple) and query_type[0] == "hotel":
        params = query_type[1]
        hotel_data = get_hotel_recommendations(
            location=params["location"],
            budget=params["budget"],
            preferences=params["preferences"]
        )
        
        if hotel_data["hotels"]:
            hotels_html = "\n".join([f"""
            <div class="hotel-card" style="background: #f5f5f5; border-radius: 8px; padding: 12px; margin-bottom: 15px;">
                <img src="{hotel['image']}" alt="{hotel['name']}" style="width: 100%; height: 140px; object-fit: cover; border-radius: 6px; margin-bottom: 10px;">
                <h4 style="color: #333; margin: 0;">{hotel['name']}</h4>
                <div style="color: #666; font-size: 14px; margin: 5px 0;">
                    <span style="color: gold;">{hotel['rating']}</span>
                    <span style="margin-left: 10px;">{hotel['distance']}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="background: #10a37f; color: white; padding: 3px 6px; border-radius: 4px; font-size: 12px;">
                        {hotel['price']}
                    </span>
                    <a href="{hotel['link']}" target="_blank" style="background: #10a37f; color: white; padding: 5px 10px; border-radius: 4px; text-decoration: none; font-size: 12px;">
                        View Deal
                    </a>
                </div>
                <button class="action-btn track-price-btn" data-hotel-id="{hotel['hotel_id']}" data-hotel-name="{hotel['name']}" style="margin-top: 10px; width: 100%;">
                    Track Price
                </button>
            </div>
            """ for hotel in hotel_data["hotels"]])
            
            response = f"""
            <div style="margin-bottom: 15px;">
                <h3 style="color: #333; margin-bottom: 10px;">🏨 Hotels in {params['location']}</h3>
                <p style="color: #666; font-size: 14px; margin-bottom: 15px;">
                    {f"Showing options under ${params['budget']}" if params['budget'] else "Recommended stays"}
                </p>
                {hotels_html}
            </div>
            """
        else:
            response = "No hotels found matching your criteria. Try these alternatives:<br>"
            for fallback in hotel_data["fallbacks"]:
                response += f"""<a href="{fallback['link']}" target="_blank" style="color: #10a37f; text-decoration: none; display: block; margin: 5px 0;">• {fallback['name']}</a>"""
        
        return response
    
    elif isinstance(query_type, tuple) and query_type[0] == "weather":
        weather_data = get_weather(query_type[1])
        return format_weather(weather_data) if weather_data else predefined_responses["error"]
    
    elif query_type in predefined_responses:
        return predefined_responses[query_type]
    
    else:
        try:
            model = genai.GenerativeModel('gemini-1.5-pro-latest')
            response = model.generate_content(user_input)
            return response.text
        except Exception as e:
            logger.error(f"API error: {e}")
            return predefined_responses["error"]

# Track hotel price changes with Fetch.ai agent
@agent.on_interval(period=3600)  # Check every hour
async def track_hotel_prices(ctx: Context):
    """Check hotel prices and send alerts when prices drop"""
    try:
        # Get all tracked hotels from storage
        tracked_hotels = ctx.storage.get("tracked_hotels") or {}
        
        if not tracked_hotels:
            return
        
        token = get_amadeus_token()
        if not token:
            ctx.logger.error("Failed to get Amadeus token for price tracking")
            return
        
        for hotel_id, hotel_data in tracked_hotels.items():
            try:
                # Get current price for the hotel
                url = f"https://test.api.amadeus.com/v2/shopping/hotel-offers/by-hotel"
                headers = {
                    "Authorization": f"Bearer {token}"
                }
                params = {
                    "hotelId": hotel_id,
                    "checkInDate": hotel_data.get("check_in", "2023-10-20"),
                    "checkOutDate": hotel_data.get("check_out", "2023-10-21"),
                    "adults": 1
                }
                
                response = requests.get(url, headers=headers, params=params)
                if response.status_code == 200:
                    data = response.json()
                    if "data" in data and data["data"]:
                        current_price = None
                        for offer in data["data"]["offers"]:
                            if "price" in offer and "total" in offer["price"]:
                                current_price = float(offer["price"]["total"])
                                break
                        
                        if current_price is not None:
                            threshold_price = hotel_data.get("threshold_price", float('inf'))
                            
                            # If current price is lower than threshold, send alert
                            if current_price < threshold_price:
                                alert_message = f"🏨 Price Drop Alert! {hotel_data.get('name', 'Hotel')} price has dropped to {current_price} (was {hotel_data.get('initial_price', 'N/A')})!"
                                ctx.logger.info(alert_message)
                                
                                # Send notification to wallet if configured
                                if MY_WALLET_ADDRESS != "fetch1___":
                                    await ctx.send_wallet_message(MY_WALLET_ADDRESS, alert_message)
                                
                                # Update threshold to current price (so we only alert on further drops)
                                hotel_data["threshold_price"] = current_price
                                tracked_hotels[hotel_id] = hotel_data
                                ctx.storage.set("tracked_hotels", tracked_hotels)
            except Exception as e:
                ctx.logger.error(f"Error tracking hotel {hotel_id}: {e}")
                continue
    
    except Exception as e:
        ctx.logger.error(f"Hotel price tracking error: {e}")
# 2. Add this route handler for flight search form submission
@app.route("/search_flights", methods=["POST"])
def search_flights():
    try:
        origin = request.form.get('flight_origin', '').strip().upper()
        destination = request.form.get('flight_destination', '').strip().upper()
        departure_date = request.form.get('flight_departure_date', '')
        return_date = request.form.get('flight_return_date', '')
        adults = int(request.form.get('flight_adults', 1))
        
        if not origin or not destination or not departure_date:
            return {"status": "error", "message": "Missing required flight information"}
        
        # Use empty string to indicate no return date
        return_date = return_date if return_date else None
        
        # Search flights using Amadeus API
        flights_data = search_flights_amadeus(origin, destination, departure_date, return_date, adults)
        flights = process_amadeus_flights(flights_data)
        
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        # Format flight results for chat
        if flights:
            flight_html = "<div style='margin-bottom: 15px;'>"
            flight_html += f"<h3 style='color: #333; margin-bottom: 10px;'>✈️ Flights from {origin} to {destination}</h3>"
            flight_html += f"<p style='color: #666; font-size: 14px; margin-bottom: 15px;'>Showing {len(flights)} flight options</p>"
            
            for flight in flights:
                flight_html += f"""
                <div class="hotel-card" style="background: #f5f5f5; border-radius: 8px; padding: 12px; margin-bottom: 15px;">
                    <h4 style="color: #333; margin: 0;">{flight['airline']}</h4>
                    <div style="color: #666; font-size: 14px; margin: 5px 0;">
                        <span>Flight: {flight['flight_numbers']}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin: 10px 0;">
                        <div>
                            <div style="font-weight: bold;">{flight['departure']}</div>
                            <div style="color: #666; font-size: 12px;">{origin}</div>
                        </div>
                        <div style="text-align: center; flex-grow: 1; padding: 0 10px;">
                            <div style="font-size: 12px; color: #666;">{flight['duration']}</div>
                            <div style="border-top: 1px solid #ddd; margin: 5px 0;"></div>
                            <div style="font-size: 12px; color: #666;">{flight['stops']}</div>
                        </div>
                        <div style="text-align: right;">
                            <div style="font-weight: bold;">{flight['arrival']}</div>
                            <div style="color: #666; font-size: 12px;">{destination}</div>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="background: #10a37f; color: white; padding: 3px 6px; border-radius: 4px; font-size: 12px;">
                            {flight['price']}
                        </span>
                        <a href="{flight['booking_link']}" target="_blank" style="background: #10a37f; color: white; padding: 5px 10px; border-radius: 4px; text-decoration: none; font-size: 12px;">
                            Select
                        </a>
                    </div>
                </div>
                """
            
            flight_html += "</div>"
            response = flight_html
        else:
            response = """
            <div style="text-align: center; padding: 20px; background: #f8d7da; border-radius: 8px; color: #721c24;">
                <h3>No flights found</h3>
                <p>Try different dates or airports</p>
            </div>
            """
        
        # Add response to chat history
        session['chat_history'].append({"type": "user", "content": f"Search flights from {origin} to {destination} on {departure_date}"})
        session['chat_history'].append({"type": "bot", "content": response})
        session.modified = True
        
        return redirect('/')
    
    except Exception as e:
        logger.error(f"Flight search error: {e}")
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        error_message = f"Error searching for flights: {str(e)}"
        session['chat_history'].append({"type": "bot", "content": error_message})
        session.modified = True
        
        return redirect('/')
@app.route("/submit", methods=["POST"])
def handle_message():
    data = request.get_json()
    # Process incoming message
    return {"status": "success"}
# Add hotel to tracking
@app.route("/track_hotel", methods=["POST"])
def track_hotel():
    try:
        data = request.get_json()
        hotel_id = data.get("hotel_id")
        hotel_name = data.get("hotel_name")
        price = data.get("price", 0)
        threshold = data.get("threshold", price * 0.9)  # Default 10% reduction
        
        if not hotel_id or not hotel_name:
            return {"status": "error", "message": "Missing hotel information"}
        
        # Store tracking data
        # In a real implementation, we would store this in a database
        # Here we're just simulating storage in the agent context
        token = get_amadeus_token()
        if token:
            # Get current price
            url = f"https://test.api.amadeus.com/v2/shopping/hotel-offers/by-hotel"
            headers = {
                "Authorization": f"Bearer {token}"
            }
            params = {
                "hotelId": hotel_id,
                "checkInDate": "2023-10-20",  # Example dates
                "checkOutDate": "2023-10-21",
                "adults": 1
            }
            
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                current_price = 0
                if "data" in data and data["data"]:
                    for offer in data["data"]["offers"]:
                        if "price" in offer and "total" in offer["price"]:
                            current_price = float(offer["price"]["total"])
                            break
                
                # Store hotel tracking info
                tracked_hotels = agent.context.storage.get("tracked_hotels") or {}
                tracked_hotels[hotel_id] = {
                    "name": hotel_name,
                    "initial_price": current_price,
                    "threshold_price": threshold,
                    "check_in": "2023-10-20",
                    "check_out": "2023-10-21"
                }
                agent.context.storage.set("tracked_hotels", tracked_hotels)
                
                return {"status": "success", "message": f"Now tracking {hotel_name} price changes"}
        
        return {"status": "error", "message": "Failed to set up price tracking"}
    except Exception as e:
        logger.error(f"Track hotel error: {e}")
        return {"status": "error", "message": str(e)}

# Initialize Gemini model
try:
    model = genai.GenerativeModel(
        'gemini-1.5-pro-latest',
        system_instruction=f"""You are {ASSISTANT_NAME}, a helpful travel assistant. Provide:
        - Concise answers (1-2 sentences)
        - Hotel/weather info when requested
        - Friendly but professional tone""",
        safety_settings=[{f"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}]
    )
except Exception as e:
    logger.error(f"Gemini init error: {e}")
    model = None
# Restaurant Routes ▼
@app.route('/search_restaurants', methods=['POST'])
def handle_restaurant_search():
    data = request.get_json()
    location = data.get('location', 'London')
    cuisine = data.get('cuisine')
    
    restaurants = get_real_restaurants(location, cuisine)
    
    if not restaurants:
        return jsonify({
            "status": "error",
            "message": "No restaurants found",
            "fallback_link": f"https://www.opentable.com/search?city={location}"
        })
    
    return jsonify({
        "status": "success",
        "location": location,
        "results": restaurants
    })

@app.route('/book_restaurant', methods=['POST'])
def mock_book_restaurant():
    # Simulate booking (replace later with real integration)
    return jsonify({
        "status": "success",
        "confirmation": f"RES-{random.randint(1000,9999)}",
        "message": "Your table is reserved!"
    })   
@app.route("/get_live_hotels", methods=["POST"])
def get_live_hotels():
    try:
        data = request.get_json()
        location = data.get('location', '').strip()
        
        if not location:
            return {"status": "error", "message": "No location provided"}
            
        # Use Amadeus API instead of scraping
        from datetime import datetime, timedelta
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        
        hotels_data = search_hotels_amadeus(location, tomorrow, day_after)
        hotels = process_amadeus_hotels(hotels_data)
        
        if hotels:
            return {
                "status": "success", 
                "hotels": hotels
            }
        else:
            return {
                "status": "error", 
                "message": "No hotels found", 
                "hotels": []
            }
    except Exception as e:
        logger.error(f"Hotel API error: {e}")
        return {"status": "error", "message": str(e), "hotels": []}

@app.route("/", methods=["GET", "POST"])
def chat():
    global ASSISTANT_NAME, SPEECH_ENABLED, VOICE_INPUT_ENABLED
    
    # Initialize session for chat history if not exists
    if 'chat_history' not in session:
        session['chat_history'] = []
        # Get today's date for flight search form
    today = datetime.now().strftime('%Y-%m-%d')
    default_departure = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    default_return = (datetime.now() + timedelta(days=8)).strftime('%Y-%m-%d')
    show_name_input = False
    
    if request.method == "POST":
        # Handle special commands first
        if "change_name" in request.form:
            new_name = request.form.get("user_input", "").strip()
            if new_name:
                ASSISTANT_NAME = new_name
                predefined_responses.update({
                    "greeting": f"🌍 Hi! I'm {ASSISTANT_NAME}. Ask me anything!",
                    "name": f"🤖 I'm {ASSISTANT_NAME}, your assistant"
                })
                session['chat_history'].append({"type": "bot", "content": f"✅ Now call me {ASSISTANT_NAME}!"})
            else:
                session['chat_history'].append({"type": "bot", "content": "⚠️ Please enter a valid name"})
        elif "toggle_tts" in request.form:
            SPEECH_ENABLED = not SPEECH_ENABLED
            session['chat_history'].append({"type": "bot", "content": f"🔊 TTS {'ENABLED' if SPEECH_ENABLED else 'DISABLED'}"})
        elif "toggle_voice_input" in request.form:
            VOICE_INPUT_ENABLED = not VOICE_INPUT_ENABLED
            session['chat_history'].append({"type": "bot", "content": f"🎤 Voice input {'ENABLED' if VOICE_INPUT_ENABLED else 'DISABLED'}"})
        else:
            # Handle normal user input
            user_input = request.form.get("user_input", "").strip()
            if user_input:
                session['chat_history'].append({"type": "user", "content": user_input})
                
                query_type = detect_query_type(user_input)
                bot_reply = process_query(query_type, user_input)
                
                if query_type == "change_name":
                    show_name_input = True
                else:
                    session['chat_history'].append({"type": "bot", "content": bot_reply})
        
        session.modified = True
    
    # HTML template rendering code remains the same
    return render_template_string("""
    <!-- Include HTML template from original code -->
    <!-- Add our new JavaScript to handle price tracking -->
<!DOCTYPE html>
<html>
<head>
    <title>{{ assistant_name }} Assistant</title>
    <style>
        :root {
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --bg-color: #0f172a;
            --text-color: #e2e8f0;
            --card-bg: #1e293b;
            --border-color: #334155;
            --sidebar-bg: #1e293b;
            --sidebar-text: #e2e8f0;
            --feature-bg: #334155;
            --feature-text: #e2e8f0;
            --button-text: #ffffff;
            --user-message-bg: #334155;
            --bot-message-bg: #475569;
            --header-gradient: linear-gradient(to right, #6366f1, #8b5cf6);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', sans-serif;
        }
        
        body {
            min-height: 100vh;
            background: var(--bg-color);
            color: var(--text-color);
            position: relative;
            overflow-x: hidden;
        }
        
        /* Wavy background effect */
        .wave {
            position: absolute;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 100px;
            background: url('data:image/svg+xml;utf8,<svg viewBox="0 0 1000 200" xmlns="http://www.w3.org/2000/svg"><path d="M0,100 C150,200 350,0 500,100 C650,200 850,0 1000,100 L1000,200 L0,200 Z" fill="%236366f1" /></svg>') repeat-x;
            background-size: 1000px 100px;
            opacity: 0.3;
            z-index: 0;
        }
        
        .wave1 {
            animation: wave 30s linear infinite;
            bottom: 0;
        }
        
        .wave2 {
            animation: wave2 15s linear infinite;
            opacity: 0.2;
            animation-delay: -5s;
            bottom: 10px;
        }
        
        .wave3 {
            animation: wave 30s linear infinite;
            opacity: 0.1;
            animation-delay: -2s;
            bottom: 20px;
        }
        
        @keyframes wave {
            0% { background-position-x: 0; }
            100% { background-position-x: 1000px; }
        }
        
        @keyframes wave2 {
            0% { background-position-x: 0; }
            100% { background-position-x: -1000px; }
        }
        
        .header {
            background: var(--header-gradient);
            color: white;
            padding: 2rem;
            text-align: center;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            position: relative;
            z-index: 10;
        }
        
        .header h1 {
            font-size: 2.25rem;
            font-weight: bold;
            margin-bottom: 0.5rem;
        }
        
        .header p {
            font-size: 1.25rem;
            font-weight: 300;
        }
        
        .main-content {
            padding: 2rem;
            max-width: 1200px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
        }
        
        .card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        
        .card {
            background: var(--card-bg);
            border-radius: 0.75rem;
            padding: 1.5rem;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            position: relative;
            overflow: hidden;
            border: 1px solid var(--border-color);
        }
        
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 15px rgba(0, 0, 0, 0.2);
        }
        
        .card:active {
            transform: scale(0.98);
        }
        
        .card-icon {
            font-size: 2.5rem;
            margin-bottom: 1rem;
            transition: transform 0.3s ease;
        }
        
        .card:hover .card-icon {
            transform: scale(1.1);
        }
        
        .card h2 {
            font-size: 1.5rem;
            font-weight: bold;
            color: var(--text-color);
            margin-bottom: 0.5rem;
        }
        
        .card p {
            color: #94a3b8;
            margin-bottom: 1.5rem;
        }
        
        .card-arrow {
            position: absolute;
            right: 1.5rem;
            bottom: 1.5rem;
            color: var(--primary);
            opacity: 0;
            transform: translateX(-0.5rem);
            transition: all 0.3s ease;
        }
        
        .card:hover .card-arrow {
            opacity: 1;
            transform: translateX(0);
        }
        /*change starts here*/
        /* Updated chat button styles */
        .chat-button {
            position: fixed;
            bottom: 1.5rem;
            right: 1.5rem;
            background: var(--header-gradient);
            color: white;
            width: 56px;
            height: 56px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            cursor: pointer;
            transition: all 0.3s ease;
            z-index: 100;
            border: none;
        }
                /* Updated chat input styles */
        .chat-input {
            padding: 0.75rem;
            border-top: 1px solid var(--border-color);
            display: flex;
            gap: 5px;
        }
        .chat-input input {
            flex-grow: 1;
            padding: 0.75rem;
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            background: var(--card-bg);
            color: var(--text-color);
            outline: none;
        }                       
        .chat-input button {
            background: var(--primary);
            color: white;
            border: none;
            padding: 0 1rem;
            border-radius: 0.5rem;
            cursor: pointer;
            transition: background 0.3s ease;
            min-width: 40px;
        }
        .chat-button:hover {
            transform: scale(1.1);
            box-shadow: 0 10px 15px rgba(0, 0, 0, 0.2);
        }
                /* Voice input button */
        .voice-input-btn {
            background: var(--primary);
            color: white;
            border: none;
            border-radius: 0.5rem;
            width: 40px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: background 0.3s ease;
        }
        
        /* Chat overlay */
        .chat-container {
            position: fixed;
            bottom: 0;
            right: 0;
            width: 100%;
            max-width: 24rem;
            height: 0;
            background: var(--card-bg);
            border-radius: 0.75rem 0.75rem 0 0;
            box-shadow: 0 -4px 6px rgba(0, 0, 0, 0.1);
            transition: all 0.5s ease;
            overflow: hidden;
            z-index: 90;
            border: 1px solid var(--border-color);
        }
        
        .chat-container.open {
            height: 24rem;
            bottom: 1.5rem;
            right: 1.5rem;
            border-radius: 0.75rem;
        }
        
        .chat-header {
            background: var(--header-gradient);
            color: white;
            padding: 1rem;
            border-radius: 0.75rem 0.75rem 0 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chat-header h3 {
            font-weight: bold;
        }
        
        .chat-header button {
            background: none;
            border: none;
            color: white;
            cursor: pointer;
        }
        
        .chat-messages {
            padding: 1rem;
            height: calc(100% - 7rem);
            overflow-y: auto;
        }
        
        .message {
            margin-bottom: 0.75rem;
            max-width: 80%;
        }
        
        .user-message {
            margin-left: auto;
            text-align: right;
        }
        
        .bot-message {
            margin-right: auto;
            text-align: left;
        }
        
        .message-content {
            display: inline-block;
            padding: 0.75rem;
            border-radius: 0.75rem;
            font-size: 0.875rem;
        }
        
        .user-message .message-content {
            background: var(--primary);
            color: white;
            border-bottom-right-radius: 0;
        }
        
        .bot-message .message-content {
            background: var(--feature-bg);
            color: var(--text-color);
            border-bottom-left-radius: 0;
        }
        
        .chat-input {
            padding: 0.75rem;
            border-top: 1px solid var(--border-color);
            display: flex;
        }
        
        .chat-input input {
            flex-grow: 1;
            padding: 0.75rem;
            border: 1px solid var(--border-color);
            border-radius: 0.5rem 0 0 0.5rem;
            background: var(--card-bg);
            color: var(--text-color);
            outline: none;
        }
        
        .chat-input button {
            background: var(--primary);
            color: white;
            border: none;
            padding: 0 1rem;
            border-radius: 0 0.5rem 0.5rem 0;
            cursor: pointer;
            transition: background 0.3s ease;
        }
        
        .chat-input button:hover {
            background: var(--primary-dark);
        }
        
        /* Animation classes */
        .animate-pulse {
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
        
        /* Ripple effect */
        .ripple {
            position: absolute;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.3);
            transform: scale(0);
            animation: ripple 0.6s linear;
            pointer-events: none;
        }
        
        @keyframes ripple {
            to {
                transform: scale(4);
                opacity: 0;
            }
        }
        
        /* Responsive adjustments */
        @media (max-width: 768px) {
            .card-grid {
                grid-template-columns: 1fr;
            }
            
            .chat-container.open {
                width: 100%;
                height: 70vh;
                bottom: 0;
                right: 0;
                border-radius: 0.75rem 0.75rem 0 0;
            }
        }
        
        /* Existing styles for the travel assistant functionality */
        .hotel-card, .weather-card {
            background: var(--feature-bg);
            border-radius: 0.75rem;
            padding: 1rem;
            margin-bottom: 1rem;
            color: var(--text-color);
        }
        
        .forecast-link {
            color: var(--primary);
            text-decoration: none;
            font-weight: 500;
        }
        
        .action-btn {
            background: var(--primary);
            color: var(--button-text);
            border: none;
            border-radius: 0.5rem;
            padding: 0.75rem;
            cursor: pointer;
            font-weight: 500;
            margin-top: 0.5rem;
            width: 100%;
            transition: background 0.3s ease;
        }
        
        .action-btn:hover {
            background: var(--primary-dark);
        }
        
        .tts-btn {
            background: var(--primary);
            color: var(--button-text);
            border: none;
            border-radius: 0.25rem;
            padding: 0.25rem 0.5rem;
            margin-top: 0.5rem;
            cursor: pointer;
            font-size: 0.75rem;
        }
        .restaurant-card {
        background: var(--card-bg);
        border-radius: 0.75rem;
        padding: 1rem;
        margin: 1rem 0;
        border: 1px solid var(--border-color);
        transition: transform 0.3s ease;
    }
    
    .restaurant-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    
    .restaurant-card img {
        margin-bottom: 0.75rem;
        width: 100%;
        height: 150px;
        object-fit: cover;
        border-radius: 8px;
        border: 1px solid var(--border-color);
    }
    
    .restaurant-card h4 {
        color: var(--text-color);
        margin: 0.5rem 0;
        font-size: 1.1rem;
    }
    
    .restaurant-card p {
        color: #94a3b8;
        margin: 0.25rem 0;
        font-size: 0.9rem;
    }
    </style>
</head>
<body>
    <!-- Wavy background -->
    <div class="wave wave1"></div>
    <div class="wave wave2"></div>
    <div class="wave wave3"></div>
    
    <!-- Header -->
    <header class="header">
        <h1>AI Travel Assistant</h1>
        <p>Book smarter. Travel smoother.</p>
    </header>
    
    <!-- Main content -->
    <div class="main-content">
        <div class="card-grid">
            <div class="card" onclick="showPanel('flight-panel')">
                <div class="card-icon">✈️</div>
                <h2>Flights</h2>
                <p>Find and book flights with real-time AI assistance.</p>
                <div class="card-arrow">→</div>
            </div>
            
            <div class="card" onclick="showPanel('hotel-panel')">
                <div class="card-icon">🏨</div>
                <h2>Hotels</h2>
                <p>Discover and reserve your perfect stay.</p>
                <div class="card-arrow">→</div>
            </div>
            
            <div class="card" onclick="searchRestaurants()">
                <div class="card-icon">🍽️</div>
                <h2>Restaurants</h2>
                <p>Find and book tables with AI assistance.</p>
                <div class="card-arrow">→</div>
            </div>
        </div>
        
        <!-- Feature panels (hidden by default) -->
        <div id="flight-panel" class="panel" style="display: none;">
            <h3 style="margin-bottom: 1rem;">Flight Search</h3>
            <!-- Replace your existing flight search form with this -->
<form method="post" id="flightSearchForm" action="/search_flights">
    <div style="margin-bottom: 1rem;">
        <label style="display: block; margin-bottom: 0.5rem;">From (Airport Code)</label>
        <input type="text" name="flight_origin" placeholder="e.g. JFK" required 
               style="width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);"
               maxlength="3" pattern="[A-Za-z]{3}" title="3-letter airport code">
    </div>
    
    <div style="margin-bottom: 1rem;">
        <label style="display: block; margin-bottom: 0.5rem;">To (Airport Code)</label>
        <input type="text" name="flight_destination" placeholder="e.g. LHR" required 
               style="width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);"
               maxlength="3" pattern="[A-Za-z]{3}" title="3-letter airport code">
    </div>
    
    <div style="margin-bottom: 1rem;">
        <label style="display: block; margin-bottom: 0.5rem;">Departure Date</label>
        <input type="date" name="flight_departure_date" 
               value="{{ today }}" 
               min="{{ today }}" 
               style="width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);"
               required>
    </div>
    
    <div style="margin-bottom: 1rem;">
        <label style="display: block; margin-bottom: 0.5rem;">Return Date (optional)</label>
        <input type="date" name="flight_return_date" 
               min="{{ today }}" 
               style="width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);">
    </div>
    
    <div style="margin-bottom: 1rem;">
        <label style="display: block; margin-bottom: 0.5rem;">Passengers</label>
        <select name="flight_adults" style="width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);">
            <option value="1">1 Adult</option>
            <option value="2">2 Adults</option>
            <option value="3">3 Adults</option>
            <option value="4">4 Adults</option>
        </select>
    </div>
    
    <button type="submit" class="action-btn">
        Search Flights
    </button>
</form>
        </div>
        
        <div id="hotel-panel" class="panel" style="display: none;">
            <h3 style="margin-bottom: 1rem;">Hotel Search</h3>
            
            <div style="margin-bottom: 1rem;">
                <label style="display: block; margin-bottom: 0.5rem;">Location</label>
                <input type="text" id="hotelSearchInput" placeholder="City or destination" 
                       style="width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);">
            </div>
            
            <div style="margin-bottom: 1rem;">
                <label style="display: block; margin-bottom: 0.5rem;">Price Range</label>
                <select id="priceRange" style="width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);">
                    <option value="">Any price</option>
                    <option value="budget">Budget (under $100)</option>
                    <option value="moderate">Moderate ($100-$200)</option>
                    <option value="premium">Premium ($200-$300)</option>
                    <option value="luxury">Luxury ($300+)</option>
                </select>
            </div>
            
            <button class="action-btn" onclick="searchHotels()">
                Find Hotels
            </button>
            
            <div id="hotelResultsContainer" style="margin-top: 1.5rem;">
                <!-- Hotel results will be populated here -->
            </div>
        </div>
    </div>
    
    <!-- Chat button -->
    <button class="chat-button" onclick="toggleChat()">
        💬
    </button>
    
    <!-- Chat overlay -->
    <div class="chat-container" id="chatOverlay">
        <div class="chat-header">
            <h3>{{ assistant_name }} Assistant</h3>
            <button onclick="toggleChat()">✕</button>
        </div>
        
        <div class="chat-messages" id="chatHistory">
            {% for message in chat_history %}
                <div class="message {% if message.type == 'user' %}user-message{% else %}bot-message{% endif %}">
                    <div class="message-content">
                        {{ message.content | safe }}
                        {% if message.type == 'bot' and speech_enabled %}
                        <button class="tts-btn" onclick="speakText('{{ message.content | replace("'", "\\'") | striptags }}')">
                            🔊 Read Aloud
                        </button>
                        {% endif %}
                    </div>
                </div>
            {% endfor %}
            
            {% if show_name_input %}
            <div style="margin-top: 1rem;">
                <form method="post">
                    <input type="hidden" name="change_name" value="true">
                    <input type="text" name="user_input" placeholder="Enter new name" required 
                           style="width: 100%; padding: 0.75rem; margin-bottom: 0.5rem; border-radius: 0.5rem; border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-color);">
                    <input type="submit" value="Submit" class="action-btn" style="width: auto;">
                </form>
            </div>
            {% endif %}
        </div>
        
        <form method="post" class="chat-input" id="chatForm">
            {% if voice_input_enabled %}
            <button type="button" class="voice-input-btn" id="voiceInputBtn" title="Voice Input">🎤</button>
            {% endif %}
            <input type="text" name="user_input" id="userInput" placeholder="Ask me anything..." required>
            <button type="submit">➤</button>
        </form>
    </div>
    
    <!-- Chat button (placed after chat container for proper stacking) -->
    <button class="chat-button" id="chatButton" onclick="toggleChat()">
        💬
    </button>
    
    <script>
        // Updated toggleChat function
        function toggleChat() {
            const chatOverlay = document.getElementById('chatOverlay');
            const chatButton = document.getElementById('chatButton');
            
            chatOverlay.classList.toggle('open');
            
            // Toggle chat button visibility
            if (chatOverlay.classList.contains('open')) {
                chatButton.style.display = 'none';
            } else {
                chatButton.style.display = 'flex';
            }
        }
                                  
        // Auto-scroll to bottom
        const chatHistory = document.getElementById('chatHistory');
        chatHistory.scrollTop = chatHistory.scrollHeight;
        
        // Toggle chat
        function toggleChat() {
            const chatOverlay = document.getElementById('chatOverlay');
            chatOverlay.classList.toggle('open');
        }
        
        // Show panel
        function showPanel(panelId) {
            // Hide all panels
            document.querySelectorAll('.panel').forEach(panel => {
                panel.style.display = 'none';
            });
            
            // Show selected panel
            document.getElementById(panelId).style.display = 'block';
            
            // Add ripple effect
            const card = event.currentTarget;
            const ripple = document.createElement('span');
            ripple.classList.add('ripple');
            
            // Get click position
            const rect = card.getBoundingClientRect();
            const x = event.clientX - rect.left;
            const y = event.clientY - rect.top;
            
            // Position the ripple
            ripple.style.left = `${x}px`;
            ripple.style.top = `${y}px`;
            
            // Add ripple to card
            card.appendChild(ripple);
            
            // Remove ripple after animation
            setTimeout(() => {
                ripple.remove();
            }, 600);
        }
        
        // Speak text
        function speakText(text) {
            if ('speechSynthesis' in window) {
                const utterance = new SpeechSynthesisUtterance();
                utterance.text = text.replace(/<[^>]*>/g, '');
                window.speechSynthesis.speak(utterance);
            } else {
                alert("Your browser doesn't support TTS");
            }
        }
        
        // Search hotels
        function searchHotels() {
            const location = document.getElementById('hotelSearchInput').value.trim() || 'Paris';
            const priceRange = document.getElementById('priceRange').value;
            const container = document.getElementById('hotelResultsContainer');
            
            // Show loading state
            container.innerHTML = '<div style="text-align: center; padding: 1.25rem;">Searching for hotels...</div>';
            
            // Create a form and submit it to process the hotel search
            const form = document.createElement('form');
            form.method = 'post';
            form.style.display = 'none';
            
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'user_input';
            input.value = `Hotels in ${location}${priceRange ? ' ' + priceRange : ''}`;
            
            form.appendChild(input);
            document.body.appendChild(form);
            form.submit();
        }
        
        // Auto-uppercase airport codes and limit to 3 characters
        document.querySelectorAll('input[name="flight_origin"], input[name="flight_destination"]').forEach(input => {
            input.addEventListener('input', function() {
                this.value = this.value.toUpperCase().substring(0, 3);
            });
        });
        
        // Set today's date as min for date inputs
        document.addEventListener('DOMContentLoaded', function() {
            const today = new Date().toISOString().split('T')[0];
            document.querySelectorAll('input[type="date"]').forEach(input => {
                input.min = today;
            });
            
            // Initialize chat if there are messages
            if (document.querySelectorAll('#chatHistory .message').length > 0) {
                document.getElementById('chatOverlay').classList.add('open');
            }
        });
        
        // Add ripple effect to all cards
        document.querySelectorAll('.card').forEach(card => {
            card.addEventListener('click', function(e) {
                const ripple = document.createElement('span');
                ripple.classList.add('ripple');
                
                // Get click position
                const rect = this.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                
                // Position the ripple
                ripple.style.left = `${x}px`;
                ripple.style.top = `${y}px`;
                
                // Add ripple to card
                this.appendChild(ripple);
                
                // Remove ripple after animation
                setTimeout(() => {
                    ripple.remove();
                }, 600);
            });
        });
                // Voice input handler
        document.getElementById('voiceInputBtn')?.addEventListener('click', function() {
            if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
                const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
                const recognition = new SpeechRecognition();
                
                recognition.lang = 'en-US';
                recognition.interimResults = false;
                
                // Change button color to indicate recording
                this.style.background = '#dc2626';
                this.textContent = '🔴';
                
                recognition.start();
                
                recognition.onresult = function(event) {
                    const transcript = event.results[0][0].transcript;
                    document.getElementById('userInput').value = transcript;
                };
                
                recognition.onend = function() {
                    // Restore button color
                    const voiceBtn = document.getElementById('voiceInputBtn');
                    voiceBtn.style.background = '';
                    voiceBtn.textContent = '🎤';
                };
                
                recognition.onerror = function(event) {
                    alert('Voice recognition error: ' + event.error);
                    // Restore button color
                    const voiceBtn = document.getElementById('voiceInputBtn');
                    voiceBtn.style.background = '';
                    voiceBtn.textContent = '🎤';
                };
            } else {
                alert('Voice recognition not supported in this browser');
            }
        });
    async function searchRestaurants() {
    const location = prompt("Enter city:") || "London";
    const cuisine = prompt("Cuisine (optional):");
    
    try {
        const response = await fetch('/search_restaurants', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ location, cuisine })
        });
        
        const results = await response.json();
        
        if (results.status === "success") {
            displayRestaurants(results.results);
        } else {
            window.open(results.fallback_link, '_blank');
        }
    } catch (error) {
        console.error("Search failed:", error);
        alert("Error searching restaurants");
    }
}

function displayRestaurants(restaurants) {
    const container = document.getElementById('restaurantResults') || createResultsContainer();
    
    let html = '<h3>🍽️ Available Restaurants</h3>';
    restaurants.forEach(rest => {
        html += `
        <div class="restaurant-card">
            <img src="${rest.image || 'https://via.placeholder.com/300x200?text=Restaurant'}" 
                 style="width:100%; height:150px; object-fit:cover; border-radius:8px;">
            <h4>${rest.name}</h4>
            <p>${rest.cuisine} • ${rest.price_range}</p>
            <p>⭐ ${rest.rating} | ${rest.location}</p>
            <button onclick="bookRestaurant('${rest.id}')" class="action-btn">
                Book Table
            </button>
        </div>`;
    });
    
    container.innerHTML = html;
}

function createResultsContainer() {
    const div = document.createElement('div');
    div.id = 'restaurantResults';
    div.style.marginTop = '20px';
    document.querySelector('.main-content').appendChild(div);
    return div;
}

async function bookRestaurant(restId) {
    const response = await fetch('/book_restaurant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ restaurant_id: restId })
    });
    
    const result = await response.json();
    alert(result.message);
}
    </script>
</body>
</html>
    """,
    chat_history=session.get('chat_history', []),
    assistant_name=ASSISTANT_NAME,
    speech_enabled=SPEECH_ENABLED,
    voice_input_enabled=VOICE_INPUT_ENABLED,
    show_name_input=show_name_input,
    today=today,
    default_departure=default_departure,
    default_return=default_return)

if __name__ == "__main__":
    # Start the agent in the background
    import threading
    agent_thread = threading.Thread(target=agent.run)
    agent_thread.daemon = True
    agent_thread.start()
    
    # Start the Flask app
    app.run(debug=True, port=5000)
