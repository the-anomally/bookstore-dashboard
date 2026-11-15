from datetime import date
import hmac
import psycopg
from psycopg import sql
import streamlit as st

st.set_page_config(page_title="Bookstore Cloud Dashboard", layout="wide")

# --- STAFF LOGIN GATE ---
if "authenticated" not in st.session_state:
  st.session_state["authenticated"] = False


def require_login():
  if st.session_state["authenticated"]:
    return True

  st.title("Bookstore Cloud Dashboard")
  st.subheader("Staff Login")
  with st.form("login_form"):
    entered_password = st.text_input("Staff Password", type="password")
    if st.form_submit_button("Log In"):
      if hmac.compare_digest(entered_password, st.secrets["STAFF_PASSWORD"]):
        st.session_state["authenticated"] = True
        st.rerun()
      else:
        st.error("Incorrect password.")
  return False


if not require_login():
  st.stop()

# --- AUTHENTICATED DASHBOARD ---
DB_URL = st.secrets["DB_URL"]

col_title, col_logout = st.columns([6, 1])
with col_title:
  st.title("Bookstore Cloud Dashboard")
with col_logout:
  st.write("")
  if st.button("Log Out"):
    st.session_state["authenticated"] = False
    st.rerun()


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


# Load books, customers, and all rentals
book_rows, books_data = fetch_table("SELECT * FROM books ORDER BY book_id;")
customer_rows, customers_data = fetch_table(
    "SELECT * FROM customers ORDER BY customer_id;"
)
all_rentals, _ = fetch_table("""
    SELECT rental_id, book_id, customer_id, borrow_date, return_date
    FROM rentals
    ORDER BY rental_id DESC;
    """)

active_rentals = [r for r in all_rentals if r[4] is None]
returned_count = len(all_rentals) - len(active_rentals)

book_names = {r[0]: r[1] for r in book_rows}
customer_names = {r[0]: r[1] for r in customer_rows}

# --- KPI SUMMARY METRICS ---
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Books", len(book_rows))
m2.metric("Total Customers", len(customer_rows))
m3.metric("Active Rentals", len(active_rentals))
m4.metric("Returned Rentals", returned_count)

st.divider()

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

# --- SECTION 2: Database Explorer, Search & Delete Controls ---
st.subheader("Database Explorer")
tab_rentals, tab_books, tab_customers, tab_logs = st.tabs(
    ["Live Rentals (View)", "Books", "Customers", "Audit Logs (Trigger)"]
)

with tab_rentals:
  _, rentals_data = fetch_table(
      "SELECT * FROM rental_details ORDER BY rental_id DESC;"
  )

  search_term = (
      st.text_input(
          "Search Rentals",
          placeholder="Filter by book title, customer name, date, or ID...",
      )
      .strip()
      .lower()
  )
  if search_term:
    filtered_rentals = [
        row
        for row in rentals_data
        if any(search_term in str(val).lower() for val in row.values())
    ]
  else:
    filtered_rentals = rentals_data

  st.dataframe(filtered_rentals, hide_index=True, use_container_width=True)

  if all_rentals:
    with st.expander("Delete a Rental Record"):
      with st.form("delete_rental_form"):
        rental_to_delete = st.selectbox(
            "Select Rental to Delete",
            all_rentals,
            format_func=lambda r: (
                f"Rental #{r[0]}: {book_names.get(r[1], f'Book #{r[1]}')} —"
                f" {customer_names.get(r[2], f'Customer #{r[2]}')} (Borrowed"
                f" {r[3]} | {'Active' if r[4] is None else f'Returned {r[4]}'})"
            ),
        )
        confirm_rental_del = st.checkbox(
            "Confirm permanent deletion of this rental record"
        )
        remove_linked_logs = st.checkbox(
            "Also remove linked audit log entries if required by database"
            " constraints"
        )
        if st.form_submit_button("Delete Rental", type="primary"):
          if not confirm_rental_del:
            st.warning("Please tick the confirmation box first.")
          else:
            try:
              with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                  if remove_linked_logs:
                    cur.execute(
                        "DELETE FROM rental_logs WHERE rental_id = %s;",
                        (rental_to_delete[0],),
                    )
                  cur.execute(
                      "DELETE FROM rentals WHERE rental_id = %s;",
                      (rental_to_delete[0],),
                  )
              st.rerun()
            except psycopg.errors.ForeignKeyViolation:
              st.error(
                  "This rental is referenced in rental_logs. Tick the second"
                  " box above to remove its linked log entry as well."
              )
            except Exception as e:
              st.error(f"Could not delete rental: {e}")

with tab_books:
  render_add_row_form("books", "add_book_form", "Add New Book")
  st.dataframe(books_data, hide_index=True, use_container_width=True)

  if book_rows:
    with st.expander("Delete a Book"):
      with st.form("delete_book_form"):
        book_to_delete = st.selectbox(
            "Select Book to Delete",
            book_rows,
            format_func=lambda r: f"{r[0]} - {r[1]}",
        )
        confirm_book_del = st.checkbox(
            "Confirm permanent deletion of this book"
        )
        if st.form_submit_button("Delete Book", type="primary"):
          if not confirm_book_del:
            st.warning("Please tick the confirmation box first.")
          else:
            try:
              with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                  cur.execute(
                      "DELETE FROM books WHERE book_id = %s;",
                      (book_to_delete[0],),
                  )
              st.rerun()
            except psycopg.errors.ForeignKeyViolation:
              st.error(
                  "Cannot delete this book because it still has rental records."
                  " Delete its rentals first."
              )
            except Exception as e:
              st.error(f"Could not delete book: {e}")

with tab_customers:
  render_add_row_form("customers", "add_customer_form", "Add New Customer")
  st.dataframe(customers_data, hide_index=True, use_container_width=True)

  if customer_rows:
    with st.expander("Delete a Customer"):
      with st.form("delete_customer_form"):
        customer_to_delete = st.selectbox(
            "Select Customer to Delete",
            customer_rows,
            format_func=lambda r: f"{r[0]} - {r[1]}",
        )
        confirm_cust_del = st.checkbox(
            "Confirm permanent deletion of this customer"
        )
        if st.form_submit_button("Delete Customer", type="primary"):
          if not confirm_cust_del:
            st.warning("Please tick the confirmation box first.")
          else:
            try:
              with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                  cur.execute(
                      "DELETE FROM customers WHERE customer_id = %s;",
                      (customer_to_delete[0],),
                  )
              st.rerun()
            except psycopg.errors.ForeignKeyViolation:
              st.error(
                  "Cannot delete this customer because they still have rental"
                  " records. Delete their rentals first."
              )
            except Exception as e:
              st.error(f"Could not delete customer: {e}")

with tab_logs:
  _, logs_data = fetch_table("SELECT * FROM rental_logs ORDER BY log_id DESC;")
  st.dataframe(logs_data, hide_index=True, use_container_width=True)
