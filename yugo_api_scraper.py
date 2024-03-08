import requests
import schedule
import time
import logging
import os
from datetime import datetime

# Set up Pushover credentials
api_token = os.environ.get('API_TOKEN')
user_key = os.environ.get('USER_KEY')

logger = logging.getLogger(__name__)

API_PUSHOVER = "https://api.pushover.net/1/messages.json"
API_PREFIX = "https://yugo.com/en-gb/"
API_CALLS = {
    "countries": {
        "name": "countries",
        "api": "countries"
    },
    "cities": {
        "name": "cities",
        "api": "cities?countryId={}",
        "param": "countryId"
    },
    "residences": {
        "name": "residences",
        "api": "residences?cityId={}",
        "param": "contentId"
    },
    "rooms": {
        "name": "rooms",
        "api": "rooms?residenceId={}",
        "param": "residenceId"
    },
    "options": {
        "name": "tenancyOptions",
        "api": "tenancyOptionsBySSId?residenceId={}&residenceContentId={}&roomId={}",
    }
}

def check_item(data):
    try:
        n_item = int(input("Choose: ")) - 1
        assert n_item >= 0
        return data[n_item]
    except:
        print("Please, introduce a valid number!")
        return check_item(data)

def get_data(resource_name, resource_api):
    url = API_PREFIX + resource_api
    response = requests.get(url)
    data = response.json()
    print(f"\n** {resource_name.upper()} **\n")
    for i, item in enumerate(data[resource_name], start=1):
        print(f"{i}. {item['name']}")
    item_chosen = check_item(data[resource_name])
    print(f"\n{item_chosen['name']} is chosen!")
    return item_chosen

def ask_yes_no_question(question):
    while True:
        response = input(question + " (Y/N): ").strip().upper()
        if response in ['Y', 'N']:
            return response == 'Y'  # Returns True for 'Y', False for 'N'
        else:
            print("Invalid response. Please enter 'Y' for Yes or 'N' for No.")

def set_options():
    filter_by_date = ask_yes_no_question("Do you want to filter by date?")
    
    options = {}

    if filter_by_date == 'Y':
        def get_year(prompt):
            limit_year = datetime.now().year + 10
            while True:
                try:
                    year = int(input(prompt))
                    if year > 0 and year < limit_year:
                        return year
                    else:
                        print(f"Please enter a valid year between 1 and {limit_year}.")
                except ValueError:
                    print("Invalid input. Please enter a valid year.")
        from_year = get_year("From year: ")
        to_year = get_year("To year: ")
        options['from_year'] = from_year
        options['to_year'] = to_year

    options['private_bathroom'] = ask_yes_no_question("Do you want a private bathroom?")
    options['private_kitchen'] = ask_yes_no_question("Do you want a private kitchen?")

    return options

def check_arrangement(room_data, room_name):
    arrengement = room_data.get(room_name, None)
    private = None
    if arrengement:
        private = 'private' in arrengement.lower()
    return private

def check_options(city_id, my_options):
    try:
        # Make the initial GET request to retrieve room data
        residences_url = API_PREFIX + API_CALLS["residences"]["api"].format(city_id)
        residences_response = requests.get(residences_url)
        residences_data = residences_response.json()

        messages = ""
        option_count = 0

        # Iterate over the residences
        for residence in residences_data["residences"]:
            residence_id = residence["id"]
            residence_content_id = residence["contentId"]
            
            # Make the GET request to retrieve room data
            rooms_url = API_PREFIX + API_CALLS['rooms']['api'].format(residence_id)
            rooms_response = requests.get(rooms_url)
            rooms_data = rooms_response.json()

            if rooms_data == None or "rooms" not in rooms_data:
                continue
            
            # Iterate over the rooms
            for room in rooms_data[API_CALLS['rooms']['name']]:
                room_id = room['id']

                private_bathroom = check_arrangement(room, 'bathroomArrangement')
                private_kitchen = check_arrangement(room, 'kitchenArrangement')
                private_bathroom_option = my_options.get('private_bathroom', None)
                private_kitchen_option = my_options.get('private_kitchen', None)
                if private_bathroom_option and private_bathroom_option != private_bathroom or private_kitchen_option and private_kitchen_option != private_kitchen:
                    continue
                
                # Make the GET request to retrieve tenancy options
                tenancy_options_url = API_PREFIX + API_CALLS['options']['api'].format(residence_id,residence_content_id,room_id)
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

                    add_message = False
                    from_year_option = my_options.get('from_year', None)
                    to_year_option = my_options.get('to_year', None)
                    if from_year_option and to_year_option:
                        if from_year == from_year_option and to_year == to_year_option:
                            add_message = True
                    else:
                        add_message = True

                    if add_message:
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
    r = requests.post(API_PUSHOVER, data = {
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

    country = get_data(API_CALLS['countries']['name'], API_CALLS['countries']['api'])
    city = get_data(API_CALLS['cities']['name'], API_CALLS['cities']['api'].format(country[API_CALLS['cities']['param']]))
    options = set_options()

    # Execute check_options immediately
    check_options(city[API_CALLS['residences']['param']], options)

    # Schedule the job to run every minute
    schedule.every(1).minutes.do(lambda: check_options(city[API_CALLS['residences']['param']], options))
    logger.info("Job scheduled")

    # Keep the script running
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
