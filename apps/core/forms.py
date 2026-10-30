from django import forms


class StyledFormMixin:
    """Applies Bootstrap 5 classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault("class", "form-select")
            elif isinstance(w, forms.ClearableFileInput) or isinstance(w, forms.FileInput):
                w.attrs.setdefault("class", "form-control")
            else:
                w.attrs.setdefault("class", "form-control")

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                w = self.fields[name].widget
                w.attrs["class"] = (w.attrs.get("class", "") + " is-invalid").strip()
                w.attrs["aria-invalid"] = "true"


class StyledForm(StyledFormMixin, forms.Form):
    pass


class StyledModelForm(StyledFormMixin, forms.ModelForm):
    pass


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%d")


class TimeInput(forms.TimeInput):
    input_type = "time"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%H:%M")


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%dT%H:%M")
