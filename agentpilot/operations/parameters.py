from PIL import Image


class FValue:
    def __init__(self):
        self.base_value = ''
        self.accepts = ''
        self.type_str = 'string'#


class TextFValue(FValue):
    def __init__(self):
        super().__init__()
        self.accepts = 'a text value'
        self.type_str = 'string'

    def set(self, value):
        if isinstance(value, str):
            self.base_value = value
        else:
            raise TypeError(f"TextFType cannot be set with {type(value)}")


class BoolFValue(FValue):
    def __init__(self):
        super().__init__()
        self.accepts = 'a boolean value'
        self.examples = []
        self.type_str = 'boolean'

    def set(self, value):
        if isinstance(value, str):
            value = value.lower()
            self.base_value = True if value in ['true', '1', 'yes', 'y'] else None
            self.base_value = (
                False if value in ['false', '0', 'no', 'n'] else self.base_value
            )

        elif isinstance(value, bool):
            self.base_value = value
        else:
            raise TypeError(f"TextFType cannot be set with {type(value)}")

    # def get(self, return_type='STRING'):
    #     if return_type == 'STRING':
    #         return self.base_value
    #     else:
    #         raise NotImplementedError


class FileFValue(FValue):
    def __init__(self):
        super().__init__()
        self.accepts = 'a path of a file'
        self.examples = []
        self.type_str = 'string'

    def set(self, value):
        if isinstance(value, str):
            # path of the image
            self.base_value = value
        elif isinstance(value, Image.Image):
            # save image to file and set value to the path
            raise NotImplementedError
        else:
            raise TypeError(f"ImageFType cannot be set with {type(value)}")


class ImageFValue(FileFValue):
    def __init__(self):
        super().__init__()
        self.accepts = 'a path of an image'
        self.type_str = 'string'

    def set(self, value):
        if isinstance(value, str):
            self.base_value = value
        elif isinstance(value, Image.Image):
            # save image to file and set value to the path
            raise NotImplementedError
        else:
            raise TypeError(f"ImageFType cannot be set with {type(value)}")

    def get(self, return_type='PATH'):
        if return_type == 'PATH':
            return self.base_value
        else:
            raise NotImplementedError


class TimeBasedFType(FValue):
    def __init__(self):
        super().__init__()
        self.accepts = 'an expression of time represented as text'
        self.type_str = 'string'
