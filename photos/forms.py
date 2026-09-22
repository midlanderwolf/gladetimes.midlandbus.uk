from django.forms import Form, ImageField, URLField, ValidationError


class PhotoForm(Form):
    url = URLField(label="Flickr photo URL", required=False)
    image = ImageField(label="or upload a photo", required=False)

    def clean(self):
        cleaned_data = super().clean()
        if bool(cleaned_data.get("url")) == bool(cleaned_data.get("image")):
            raise ValidationError("Enter a Flickr photo URL or upload a photo")
        return cleaned_data
