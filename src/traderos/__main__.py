"""Module entrypoint so `python -m traderos <verb>` matches the console
script `traderos`. The runbooks document the `python -m traderos` form, so
this entrypoint must exist for those documented commands to execute."""

from traderos.interfaces.cli.main import main

if __name__ == "__main__":
    main()
