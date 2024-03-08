from colorama import Fore, Style
import time
from tqdm import tqdm

class ConsoleUI:
    @staticmethod
    def print_header(title):
        border = "*" * (len(title) + 6)
        print(Fore.GREEN + f"\n{border}\n** {title.upper()} **\n{border}" + Style.RESET_ALL)

    @staticmethod
    def print_item(index, name):
        print(Fore.YELLOW + f"{index}. {name}" + Style.RESET_ALL)

    @staticmethod
    def print_confirmation(item_chosen):
        print(Fore.CYAN + f"\n{item_chosen['name']} is chosen!\n" + Style.RESET_ALL)

    @staticmethod
    def show_loading_message(message):
        print(Fore.BLUE + message + Style.RESET_ALL)

    @staticmethod
    def show_error_message(message):
        print(Fore.RED + f"Error: {message}" + Style.RESET_ALL)
    
    @staticmethod
    def ask_yes_no_question(question):
        while True:
            response = input(Fore.MAGENTA + question + " (Y/N): " + Style.RESET_ALL).strip().upper()
            if response in ['Y', 'N']:
                return response == 'Y'  # Returns True for 'Y', False for 'N'
            else:
                print(Fore.RED + "Invalid response. Please enter 'Y' for Yes or 'N' for No." + Style.RESET_ALL)
    
    @staticmethod
    def get_numeric_input(prompt):
        while True:
            try:
                n = float(input(Fore.YELLOW + prompt + Style.RESET_ALL))
                if n > 0:
                    return n
                else:
                    print(Fore.RED + "Please enter a positive number." + Style.RESET_ALL)
            except ValueError:
                print(Fore.RED + "Invalid input. Please enter a valid number." + Style.RESET_ALL)
    
    @staticmethod
    def say_goodbye():
        print(Fore.LIGHTBLUE_EX + "\nThank you for using our application. Goodbye!\n" + Style.RESET_ALL)