The prompt each agent got (`{TASK}` and `{TRIAL}` filled in per run). Nothing else was provided: no data, no
path to it, and the generator (`make_data.py`) was moved out of the repository while the agents worked.

> You are writing a data-analysis script for a dataset you cannot see. Work ONLY inside `{WORKDIR}`. Do not read or
> search anywhere else on this computer, and do not look for the real data: it is deliberately unavailable to you
> and the point is to see how far a schema gets you.
>
> What you have, in `demo/briefing/`:
> - `schema.txt`: the structure of the two CSV files (field names and types, no values).
> - `sample/`: fake rows with the same shape (`customers_csv.csv`, `orders_csv.csv`). The values are meaningless
>   random strings and numbers, only the columns match. Use them to check that your script runs.
>
> The real data folder will contain `customers.csv` and `orders.csv` with the schema in `schema.txt`.
> **Real exports are messy, as real exports are.**
>
> TASK: `{TASK}` (the `ask` of one entry in `tasks.json`)
>
> Write the script to `demo/trials/{TRIAL}/solution.py`. It takes the data folder as its first command-line argument
> (`sys.argv[1]`) and prints exactly one line of JSON with the answer to stdout. Python standard library and pandas
> are available. You cannot see the real data; the user will run the script and will only be able to tell you if it
> crashes. Test it against the sample files if you like. When done, reply with just the word done.
