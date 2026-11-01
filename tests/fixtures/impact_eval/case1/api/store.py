class Store:
    def __init__(self):
        self.rows, self.sequences = [], {}

    def next_number(self, business_id):
        number = self.sequences.get(business_id, 0) + 1
        self.sequences[business_id] = number
        return number

    def save(self, row):
        self.rows.append(row)

    def list(self, business_id):
        return [row for row in self.rows if row['business_id'] == business_id]
