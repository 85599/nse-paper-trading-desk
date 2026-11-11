name: Bug or crash
description: A command traceback, a dashboard button that does nothing, a build that fails
labels: ["bug"]
body:
  - type: textarea
    id: what
    attributes:
      label: What broke
      description: the command or button, what happened, what you expected
      placeholder: desk.py scan daily --index "Nifty IT" dies with a traceback after the feed rate-limits
    validations:
      required: true
  - type: textarea
    id: trace
    attributes:
      label: Output
      render: text
      description: the full traceback or the dashboard console error
  - type: input
    id: env
    attributes:
      label: Environment
      description: the == environment == block of `python scripts/desk.py doctor`, with your local paths redacted
      placeholder: Windows 11, python 3.14.6, engine present, node present
    validations:
      required: true
  - type: checkboxes
    id: terms
    attributes:
      label: Nothing live in this report
      description: no broker credentials, no order ids, no real account figures
      options:
        - label: I redacted anything private
          required: true
