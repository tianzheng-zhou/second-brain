import chainlit.types
print(dir(chainlit.types))
try:
    from chainlit.types import StepDict
    print("StepDict found")
except ImportError:
    print("StepDict NOT found")
