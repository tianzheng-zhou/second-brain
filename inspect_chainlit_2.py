try:
    from chainlit.step import StepDict
    print("StepDict found in chainlit.step")
except ImportError:
    print("StepDict NOT found in chainlit.step")

try:
    from chainlit.element import ElementDict
    print("ElementDict found in chainlit.element")
except ImportError:
    print("ElementDict NOT found in chainlit.element")
