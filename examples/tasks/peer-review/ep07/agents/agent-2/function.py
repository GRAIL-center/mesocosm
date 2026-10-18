def fizzbuzz(n):
    """Return 'FizzBuzz' if n is divisible by 15, 'Fizz' if by 3, 'Buzz' if by 5, otherwise str(n)."""
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)
