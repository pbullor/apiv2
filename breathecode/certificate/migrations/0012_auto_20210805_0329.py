# Generated by Django 3.2.6 on 2021-08-05 03:29

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('certificate', '0011_auto_20210701_1941'),
    ]

    operations = [
        migrations.AddField(
            model_name='layoutdesign',
            name='preview_url',
            field=models.CharField(default=None, max_length=250, null=True),
        ),
        migrations.AddField(
            model_name='userspecialty',
            name='issued_at',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
    ]
