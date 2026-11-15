from datetime import date
import psycopg
import streamlit as st

DB_URL = st.secrets["DB_URL"]

st.title("Bookstore Cloud Dashboard")


def fetch_table(query):
  with psycopg.connect(DB_URL) as conn:
    with conn.cursor() as cur:
      cur.execute(query)
      rows = cur.fetchall()
      columns = [desc[0] for desc in cur.description]
  return rows, [dict(zip(columns, row)) for row in rows]


# Load books and customers first to populate the dropdown menus
book_rows, books_data = fetch_table("SELECT * FROM books ORDER BY book_id;")
customer_rows, customers_data = fetch_table(
    "SELECT * FROM customers ORDER BY customer_id;"
)

# --- SECTION 1: Checkout Form with Dropdowns ---
st.subheader("Check Out a Book")
if book_rows and customer_rows:
  with st.form("new_rental_form", clear_on_submit=True):
    selected_book = st.selectbox(
        "Select Book",
        book_rows,
        format_func=lambda row: f"{row[0]} - {row[1]}",
    )
    selected_customer = st.selectbox(
        "Select Customer",
        customer_rows,
        format_func=lambda row: f"{row[0]} - {row[1]}",
    )
    submitted = st.form_submit_button("Record Rental")

    if submitted:
      with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
          cur.execute(
              "INSERT INTO rentals (book_id, customer_id, borrow_date) VALUES"
              " (%s, %s, %s);",
              (selected_book[0], selected_customer[0], date.today()),
          )
      st.success(f"Checked out '{selected_book[1]}' to {selected_customer[1]}!")
else:
  st.warning(
      "Add at least one book and one customer in Neon to enable checkouts."
  )

# --- SECTION 2: Live Database Tables in Tabs ---
st.subheader("Database Explorer")
tab_rentals, tab_books, tab_customers, tab_logs = st.tabs(
    ["Live Rentals (View)", "Books", "Customers", "Audit Logs (Trigger)"]
)

with tab_rentals:
  _, rentals_data = fetch_table(
      "SELECT * FROM rental_details ORDER BY rental_id DESC;"
  )
  st.dataframe(rentals_data, hide_index=True)

with tab_books:
  st.dataframe(books_data, hide_index=True)

with tab_customers:
  st.dataframe(customers_data, hide_index=True)

with tab_logs:
  _, logs_data = fetch_table("SELECT * FROM rental_logs ORDER BY log_id DESC;")
  st.dataframe(logs_data, hide_index=True)
