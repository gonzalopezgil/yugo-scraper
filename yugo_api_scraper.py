import requests
import schedule
import time
import logging
import os

# Set up Pushover credentials
api_token = os.environ.get('API_TOKEN')
user_key = os.environ.get('USER_KEY')

logger = logging.getLogger(__name__)

def check_options():
    try:
        # Make the initial GET request to retrieve room data
        residences_url = "https://yugo.com/es-es/residences?cityId=217156"
        residences_response = requests.get(residences_url)
        residences_data = residences_response.json()

        messages = ""
        option_count = 0

        # Iterate over the residences
        for residence in residences_data["residences"]:
            residence_id = residence["id"]
            residence_content_id = residence["contentId"]
            
            # Make the GET request to retrieve room data
            rooms_url = f"https://yugo.com/es-es/rooms?residenceId={residence_id}"
            rooms_response = requests.get(rooms_url)
            rooms_data = rooms_response.json()

            if rooms_data == None or "rooms" not in rooms_data:
                continue
            
            # Iterate over the rooms
            for room in rooms_data["rooms"]:
                room_id = room["id"]
                
                # Make the GET request to retrieve tenancy options
                tenancy_options_url = f"https://yugo.com/es-es/tenancyOptionsBySSId?residenceId={residence_id}&residenceContentId={residence_content_id}&roomId={room_id}"
                tenancy_options_response = requests.get(tenancy_options_url)
                tenancy_options_data = tenancy_options_response.json()

                if tenancy_options_data == None or "tenancy-options" not in tenancy_options_data:
                    continue
                
                # Iterate over the tenancy options
                for option in tenancy_options_data["tenancy-options"]:
                    from_year = option["fromYear"]
                    to_year = option["toYear"]
                    option_name = option["tenancyOption"][0]["name"]
                    option_count += 1

                    if from_year == int(2023) and to_year == int(2024) and option_name and option_name not in ["Semester 2", "Semester 3"]:
                        message = f"Residence: {residence['name']}, Room: {room['name']}, Option: {option_name}"
                        messages += message + "\n"
                        
        if messages != "":
            # Send Pushover notification
            send_notification(messages)
        else:
            logger.info(f"Scraped {option_count} options. No available rooms found")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        send_notification(str(e))   

def send_notification(message):
    if message == None or message == "":
        return
    
    # Send Pushover notification
    r = requests.post("https://api.pushover.net/1/messages.json", data = {
        "token": api_token,
        "user": user_key,
        "message": message,
    })
    
    if r.status_code != 200:
        logger.error(f"Error sending notification: {r.text}")
    else:
        logger.info(f"Notification sent: {message}")

def set_up_logging():
    # Set up logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Create a console handler to print logs in the console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Create a file handler to write logs to a file
    file_handler = logging.FileHandler("scraper.log")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info("Logging set up")

def main():
    set_up_logging()

    # Schedule the job to run every minute
    schedule.every(1).minutes.do(check_options)
    logger.info("Job scheduled")

    # Keep the script running
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
