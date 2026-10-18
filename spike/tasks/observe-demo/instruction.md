You are working in an empty project directory. Build a tiny command-line word counter in Python.

1. Create `wc.py`: given a file path as its only argument, print the number of lines, words and characters in that file, one per line, labelled.
2. Create `sample.txt` containing three short lines of text.
3. Run `python3 wc.py sample.txt` and check that the three numbers are correct.
4. Create `tests/test_wc.py` with pytest tests for an empty file and for `sample.txt`, then run `python3 -m pytest -q` and make the tests pass.
5. Use the `task` tool to delegate one piece of work to a subagent: writing `README.md` with a usage section and one example run. Wait for it to finish.
6. Reply with two sentences: what you built and what the test run printed.
