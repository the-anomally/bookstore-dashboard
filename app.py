from datetime import date
import psycopg
from psycopg import sql
import streamlit as st

DB_URL = st.secrets["DB_URL"]

st.set_page_config(page_title="Bookstore Cloud Dashboard", layout="wide")
st.title("Bookstore Cloud Dashboard")


def fetch_table(query, params=None):
  with psycopg.connect(DB_URL) as conn:
    with conn.cursor() as cur:
      cur.execute(query, params or ())
      rows = cur.fetchall()
      columns = [desc[0] for desc in cur.description]
  return rows, [dict(zip(columns, row)) for row in rows]


# Ensure rentals has a return_date column for tracking returns
with psycopg.connect(DB_URL) as conn:
  with conn.cursor() as cur:
    cur.execute("ALTER TABLE rentals ADD COLUMN IF NOT EXISTS return_date DATE;")


def render_add_row_form(table_name, form_key, button_label):
  col_rows, _ = fetch_table(
      """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_default IS NULL
        ORDER BY ordinal_position;
        """,
      (table_name,),
  )
  with st.expander(f"+ {button_label}"):
    with st.form(form_key, clear_on_submit=True):
      values = {}
      for col_name, data_type in col_rows:
        label = col_name.replace("_", " ").title()
        if data_type in ("integer", "bigint", "smallint"):
          values[col_name] = st.number_input(label, step=1, value=0)
        elif data_type in ("numeric", "decimal", "real", "double precision"):
          values[col_name] = st.number_input(label, step=0.01, value=0.0)
        elif data_type == "date":
          values[col_name] = st.date_input(label, value=date.today())
        else:
          text_val = st.text_input(label)
          values[col_name] = text_val.strip() if text_val.strip() else None

      if st.form_submit_button(button_label):
        try:
          cols = list(values.keys())
          query = sql.SQL("INSERT INTO {} ({}) VALUES ({});").format(
              sql.Identifier(table_name),
              sql.SQL(", ").join(map(sql.Identifier, cols)),
              sql.SQL(", ").join(sql.Placeholder() * len(cols)),
          )
          with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
              cur.execute(query, [values[c] for c in cols])
          st.success(f"Saved to {table_name}!")
          st.rerun()
        except Exception as e:
          st.error(f"Could not save record: {e}")


# Load current books, customers, and active unreturned rentals
book_rows, books_data = fetch_table("SELECT * FROM books ORDER BY book_id;")
customer_rows, customers_data = fetch_table(
    "SELECT * FROM customers ORDER BY customer_id;"
)
active_rentals, _ = fetch_table("""
    SELECT rental_id, book_id, customer_id, borrow_date
    FROM rentals
    WHERE return_date IS NULL
    ORDER BY rental_id DESC;
    """)

book_names = {r[0]: r[1] for r in book_rows}
customer_names = {r[0]: r[1] for r in customer_rows}

# --- SECTION 1: Check Out & Return Forms Side by Side ---
col_checkout, col_return = st.columns(2)

with col_checkout:
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
      if st.form_submit_button("Record Rental"):
        with psycopg.connect(DB_URL) as conn:
          with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rentals (book_id, customer_id, borrow_date) VALUES"
                " (%s, %s, %s);",
                (selected_book[0], selected_customer[0], date.today()),
            )
        st.rerun()
  else:
    st.info("Add a book and customer below to start checking out books.")

with col_return:
  st.subheader("Return a Book")
  if active_rentals:
    with st.form("return_rental_form", clear_on_submit=True):
      selected_rental = st.selectbox(
          "Select Active Rental",
          active_rentals,
          format_func=lambda r: (
              f"Rental #{r[0]}: {book_names.get(r[1], f'Book #{r[1]}')} —"
              f" {customer_names.get(r[2], f'Customer #{r[2]}')} (Borrowed"
              f" {r[3]})"
          ),
      )
      if st.form_submit_button("Mark Book Returned"):
        with psycopg.connect(DB_URL) as conn:
          with conn.cursor() as cur:
            cur.execute(
                "UPDATE rentals SET return_date = %s WHERE rental_id = %s;",
                (date.today(), selected_rental[0]),
            )
        st.rerun()
  else:
    st.info("No active unreturned rentals right now.")

# --- SECTION 2: Database Explorer & Inventory Forms ---
st.subheader("Database Explorer")
tab_rentals, tab_books, tab_customers, tab_logs = st.tabs(
    ["Live Rentals (View)", "Books", "Customers", "Audit Logs (Trigger)"]
)

with tab_rentals:
  _, rentals_data = fetch_table(
      "SELECT * FROM rental_details ORDER BY rental_id DESC;"
  )
  st.dataframe(rentals_data, hide_index=True, use_container_width=True)

with tab_books:
  render_add_row_form("books", "add_book_form", "Add New Book")
  st.dataframe(books_data, hide_index=True, use_container_width=True)

with tab_customers:
  render_add_row_form("customers", "add_customer_form", "Add New Customer")
  st.dataframe(customers_data, hide_index=True, use_container_width=True)

with tab_logs:
  _, logs_data = fetch_table("SELECT * FROM rental_logs ORDER BY log_id DESC;")
  st.dataframe(logs_data, hide_index=True, use_container_width=True)
