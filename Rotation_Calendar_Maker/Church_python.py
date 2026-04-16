import csv
import random
from datetime import datetime, timedelta

def get_user_names():
    print("Enter the names for this rotation:")
    names = []
    for i in range(9):
        name = input(f"Person {i+1}: ").strip()
        if not name:
            raise ValueError("Name cannot be empty.")
        names.append(name)
    return names

def generate_weekly_schedule(names, start_date):
    if len(names) != 9:
        raise ValueError("Exactly 9 names are required.")

    today = start_date
    end_of_year = datetime(start_date.year, 12, 31)

    schedule = []
    rotation_index = 0
    week_number = 1

    while today <= end_of_year:
        week_start = today
        week_end = today + timedelta(days=6)

        # Ensure we don't go past Dec 31
        if week_end > end_of_year:
            week_end = end_of_year

        assigned_person = names[rotation_index]

        schedule.append({
            "Week #": week_number,
            "Start Date": week_start.strftime("%Y-%m-%d"),
            "End Date": week_end.strftime("%Y-%m-%d"),
            "On Duty": assigned_person
        })

        # Rotate to next person
        rotation_index = (rotation_index + 1) % len(names)
        week_number += 1
        today += timedelta(days=7)

    return schedule

def write_to_csv(schedule, filename="weekly_rotation.csv"):
    fieldnames = ["Week #", "Start Date", "End Date", "On Duty"]

    with open(filename, mode="w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(schedule)

    print(f"\nCSV file '{filename}' created successfully.")

def main():
    names = get_user_names()

    # Shuffle names once to create a random rotation order
    random.shuffle(names)

    print("\nRandomized rotation order:")
    for i, name in enumerate(names, 1):
        print(f"{i}. {name}")

    start_input = input("Enter start date (YYYY-MM-DD) or press Enter for today: ").strip()
    if start_input:
        start_date = datetime.strptime(start_input, "%Y-%m-%d")
    else:
        start_date = datetime.today()

    schedule = generate_weekly_schedule(names, start_date)
    write_to_csv(schedule)

if __name__ == "__main__":
    main()