import csv
import json
import sys
import socketio
from jsonschema import validate, exceptions

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QTableWidget,
    QHeaderView,
    QTableWidgetItem,
    QTextEdit,
    QStyledItemDelegate,
)
from PySide6.QtGui import QTextOption
import socketio.exceptions


SERVER_URL = "http://127.0.0.1:5000"
UNCONNECTED_IOT_DEVICES_FILENAME = "unconnected_iot_devices.csv"
DEVICE_STATE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "fieldName": {"type": "string"},
            "datatype": {"type": "string"},
            "value": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "boolean"},
                ]
            },
        },
        "required": ["fieldName", "datatype", "value"],
    },
}


def read_unconnected_iot_devices():
    devices = []
    with open(UNCONNECTED_IOT_DEVICES_FILENAME, "r") as file:
        devices = [device for device in csv.DictReader(file)]
    for device in devices:
        device["state"] = json.loads(device["state"])
        device["connected"] = device["connected"] == "true"
        for entry in device["state"]:
            if entry["datatype"] == "boolean":
                entry["value"] = bool(entry["value"])
    return devices


class MultiLineDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QTextEdit(parent)
        editor.setAcceptRichText(False)
        editor.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        return editor

    def setEditorData(self, editor, index):
        value = index.model().data(index, 0)
        editor.setPlainText(value)

    def setModelData(self, editor, model, index):
        text = editor.toPlainText()
        model.setData(index, text)


class SpoofApp(QMainWindow):
    update_table = Signal()

    def __init__(self):
        super().__init__()
        self.devices = read_unconnected_iot_devices()
        self.internal_change = True
        self.setup_gui()
        self.internal_change = False

        self.update_table.connect(self.populate_table)

        self.sio = socketio.Client()
        self.sio.on("connected_iot_devices", self.receive_connected_iot_devices)
        self.sio.on("server_iot_device_update", self.receive_iot_device_update)
        self.connected = False

    def setup_gui(self):
        self.setWindowTitle("EcoCare Spoof App")

        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        connect_action = file_menu.addAction("Connect")
        connect_action.triggered.connect(self.connect_handler)
        disconnect_action = file_menu.addAction("Disconnect")
        disconnect_action.triggered.connect(self.disconnect_handler)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        devices_layout = QVBoxLayout()

        self.table = QTableWidget(len(self.devices), 7)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(
            [
                "Connected",
                "IP Address",
                "Name",
                "Description",
                "State",
                "Status",
                "Fault",
            ]
        )
        self.table.setWordWrap(True)
        self.table.setItemDelegateForColumn(4, MultiLineDelegate())
        self.table.itemChanged.connect(self.handle_cell_change)

        self.populate_table()

        devices_layout.addWidget(self.table)

        main_layout.addLayout(devices_layout)

        central_widget = QWidget()
        central_widget.adjustSize()

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.setLayout(main_layout)
        self.resize(750, 600)

    def populate_table(self):
        self.internal_change = True
        self.table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            self.table.setItem(row, 0, QTableWidgetItem(str(device["connected"])))
            self.table.setItem(row, 1, QTableWidgetItem(device["ipAddress"]))
            self.table.setItem(row, 2, QTableWidgetItem(device["name"]))
            self.table.setItem(row, 3, QTableWidgetItem(device["description"]))
            self.table.setItem(
                row, 4, QTableWidgetItem(json.dumps(device["state"], indent=4))
            )
            self.table.setItem(row, 5, QTableWidgetItem(device["status"]))
            self.table.setItem(row, 6, QTableWidgetItem(device["faultStatus"]))

        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.ResizeToContents
        )

        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item = self.table.item(row, 1)
            if item is not None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item = self.table.item(row, 2)
            if item is not None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item = self.table.item(row, 3)
            if item is not None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.resizeRowsToContents()
        self.internal_change = False

    def handle_cell_change(self, item: QTableWidgetItem):
        if self.internal_change:
            return

        row, col = item.row(), item.column()
        text = item.text()

        ip_address_item = self.table.item(row, 1)
        if ip_address_item:
            ip_address = ip_address_item.text()
            device = self.search_devices(ip_address)
            if not device:
                return
        else:
            return

        match col:
            case 4:
                try:
                    validate(json.loads(text), DEVICE_STATE_SCHEMA)
                    device["state"] = json.loads(text)
                except exceptions.ValidationError as _:
                    self.internal_change = True
                    self.table.setItem(
                        row,
                        col,
                        QTableWidgetItem(json.dumps(device["state"], indent=4)),
                    )
                    self.internal_change = False
                    return
            case 5:
                if text != "On" and text != "Off":
                    self.internal_change = True
                    self.table.setItem(
                        row, col, QTableWidgetItem(str(device["status"]))
                    )
                    self.internal_change = False
                    return
                device["status"] = text
            case 6:
                if text != "Ok" and text != "Fault":
                    self.internal_change = True
                    self.table.setItem(
                        row, col, QTableWidgetItem(str(device["faultStatus"]))
                    )
                    self.internal_change = False
                    return
                device["faultStatus"] = text

        self.send_iot_device_update(device)

    def connect_handler(self):
        try:
            self.sio.connect(SERVER_URL, transports=["websocket"])
        except socketio.exceptions.ConnectionError:
            return

        self.connected = True
        self.send_unconnected_iot_devices()

    def closeEvent(self, event):
        self.disconnect_handler()

    def disconnect_handler(self):
        if not self.connected:
            return

        self.sio.disconnect()
        self.connected = False

    def receive_connected_iot_devices(self, devices):
        for device in devices:
            device["connected"] = True
            if self.search_devices(device["ipAddress"]) is None:
                self.devices.append(device)

        self.update_table.emit()

    def receive_iot_device_update(self, update):
        device = self.search_devices(update["ipAddress"])
        if device:
            device["state"] = update["state"]
            device["status"] = update["status"]
            device["connected"] = True

        self.update_table.emit()

    def send_unconnected_iot_devices(self):
        if not self.connected:
            return

        unconnected_iot_devices = [
            device.copy() for device in self.devices if not device["connected"]
        ]
        for device in unconnected_iot_devices:
            del device["connected"]

        self.sio.emit(event="unconnected_iot_devices", data=unconnected_iot_devices)

    def send_iot_device_update(self, device: dict[str, str | int | float | bool]):
        if not device["connected"]:
            return

        updated_device = device.copy()
        del updated_device["connected"]

        self.sio.emit(event="spoof_app_iot_device_update", data=updated_device)

    def search_devices(
        self, ip_address: str
    ) -> dict[str, str | int | float | bool] | None:
        for device in self.devices:
            if device["ipAddress"] == ip_address:
                return device


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SpoofApp()
    window.show()
    sys.exit(app.exec())
