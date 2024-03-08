# Yugo Scraper 🏘️

Yugo Scraper is a Python-based tool 🛠 designed to help students find accommodations 🏠 by scraping the [Yugo](https://yugo.com/en-gb/home) platform. It allows users to filter residences by various criteria such as city, room arrangements, price, and tenancy dates. Additionally, it supports notifications on the phone 📱 through Pushover to keep users updated on new matching room options.

## 🌟 Features

- **City-wise Search**: The tool allows searching for accommodations in different cities 🏙 available on the Yugo platform.
- **Residence Filtering**: Users can specify preferences for room arrangements (e.g., private bathroom/kitchen), price range 💸, and tenancy dates 📅.
- **Console UI**: A user-friendly console interface guides users through the process of setting their preferences and displays matching accommodations 🖥.
- **Notifications**: Users can opt-in to receive real-time notifications on the phone via Pushover 📲 when new rooms that match their preferences are found.

## 💿 Installation

1. Clone this repository:

```bash
git clone https://github.com/gonzalopezgil/yugo-scraper.git
```

2. Navigate to the cloned directory:

```bash
cd yugo-scraper
```

3. Install the required Python packages:

```bash
pip install -r requirements.txt
```

## 🔧 Configuration

Before using the scraper, you must set up your Pushover API credentials for receiving notifications:

1. Open the `config.ini` file.
2. Enter your Pushover `api_token` and `user_key` in the respective fields.

```ini
[Pushover]
api_token = YOUR_API_TOKEN_HERE
user_key = YOUR_USER_KEY_HERE
```

## 🚀 Usage

Run the main script from the command line:

```bash
python main.py
```

Follow the prompts in the console UI to set your accommodation preferences. If you choose to receive notifications, make sure your Pushover credentials are correctly set up in the `config.ini` file.

## 📦 Dependencies

Yugo Scraper relies on the following Python packages:

- requests
- schedule
- colorama
- tqdm

These dependencies are listed in `requirements.txt` and can be installed using `pip`.

## 🔑 Renting with Yugo

For renting accommodations through Yugo, please visit their official booking page:

[https://yugo.com/en-gb/booking-flow-page](https://yugo.com/en-gb/booking-flow-page)

By accessing this website, you can enter your accommodation preferences and find the rooms you've identified using this software. This is the next step for proceeding with your booking on the Yugo platform.

## :octocat: Contributing

Contributions to Yugo Scraper are welcome! Please feel free to fork the repository, make your changes, and submit a pull request.

## 📃 License

This project is licensed under the [MIT License](https://opensource.org/license/mit/) - see the LICENSE file for details.

## ⚠️ Disclaimer

This tool is intended for personal use and educational purposes. Please use it responsibly and adhere to the Yugo platform's terms of service.