Shipping needs to read package weights typed in by warehouse staff. Create
`parse_weight(text: str) -> int` that returns the weight in whole grams.

Input is a number (an integer or a decimal using "." as separator), optional whitespace, then a
unit: g, kg, lb or oz, case-insensitive. Leading and trailing whitespace is ignored.
1 lb = 453.59237 g and 1 oz = 28.349523125 g. Round to the nearest gram.
Raise ValueError for anything else, including a missing number, a missing or unknown unit,
negative values and "," as decimal separator.
