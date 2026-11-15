from datetime import date
import psycopg
import streamlit as st

# Read your Neon connection string securely from Streamlit Secrets
DB_URL = st.secrets["DB_URL"]

st.title("Bookstore Cloud Dashboard")

# --- SECTION 1: Form to log a new rental ---
st.subheader("Check Out a Book")
with st.form("new_rental_form", clear_on_submit=True):
  book_id = st.number_input("Book ID", min_value=1, step=1)
  customer_id = st.number_input("Customer ID", min_value=1, step=1)
  submitted = st.form_submit_button("Record Rental")

  if submitted:
    with psycopg.connect(DB_URL) as conn:
      with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rentals (book_id, customer_id, borrow_date) VALUES"
            " (%s, %s, %s);",
            (book_id, customer_id, date.today()),
        )
    st.success("Rental saved to Neon!")

# --- SECTION 2: Display your live SQL View ---
st.subheader("Live Rentals")
with psycopg.connect(DB_URL) as conn:
  with conn.cursor() as cur:
    cur.execute("SELECT * FROM rental_details ORDER BY rental_id DESC;")
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]

table_data = [dict(zip(columns, row)) for row in rows]
st.dataframe(table_data)