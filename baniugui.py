# coding: utf-8
#
# xiaoyu <xiaokong1937@gmail.com>
#
# 2015/01/22
#
"""
Sync tool for baniu SDK.

"""
import sys
import os
import Queue
import platform

from PySide import QtGui, QtCore
import dict4ini
from baniu.bucket import Bucket

from main_ui import Ui_MainWindow


CONFIG_FILE = "baniu.ini"
files_queue = Queue.Queue()
uploaded_queue = Queue.Queue()
__version__ = "0.1.7"
QT_VERSION_STR = "4.8"
PYSIDE_VERSION_STR = "1.2.2"


class MainWindow(QtGui.QMainWindow):
    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._thread_pool = []

        # Default open directory of QFileDialog.
        self._default_dir = '.'

        self._setup_ui()
        self._init_binding()

        self.required_fields = set([self.ui.edt_apikey,
                                    self.ui.edt_apisecret,
                                    self.ui.edt_bucket_name])

        if os.path.isfile(CONFIG_FILE):
            self._load_default_config(CONFIG_FILE)

    def _setup_ui(self):
        """
        Initial UI settings.

        """
        self.ui.progressBar.setValue(0)
        self.ui.table_widget.setColumnWidth(0, 500)

    def _init_binding(self):
        """
        Initial signal and slot.

        """
        self.ui.btn_select_files.clicked.connect(self.select_files)
        self.ui.btn_select_dir.clicked.connect(self.select_dir)
        self.ui.btn_upload.clicked.connect(self.upload)
        self.ui.btn_exit.clicked.connect(self.exit)
        self.ui.btn_clear.clicked.connect(self.clear)

        self.ui.actionSelect_Files.triggered.connect(self.select_files)
        self.ui.actionSelect_Directories.triggered.connect(self.select_dir)
        self.ui.actionUpload.triggered.connect(self.upload)
        self.ui.actionClear.triggered.connect(self.clear)
        self.ui.actionAbout.triggered.connect(self.about)
        self.ui.actionLoad_Config.triggered.connect(self.load_config)
        self.ui.actionSave_Config.triggered.connect(self.save_config)

    def clear(self):
        self.ui.table_widget.setRowCount(0)
        self.ui.table_widget.setColumnCount(2)
        self.prefix = ''

        self.ui.statusbar.showMessage("")
        self.ui.progressBar.setValue(0)
        with files_queue.mutex:
            files_queue.queue.clear()
        with uploaded_queue.mutex:
            uploaded_queue.queue.clear()

        self._thread_pool = []

    def about(self):
        title = self.tr("About Baniu CDN Upload Tool")
        msg = self.tr("""
        <b>Baniu CDN Upload Tool</b> v {0}
        <p>Copyright (c) 2015 xiaoyu .
        <p>All rights reserved.
        <p>This application can be used to upload files to Qiniu CDN.
        <p>Python {1} - Qt {2} - PySide {3} on {4}""").format(
            __version__,
            platform.python_version(),
            QT_VERSION_STR, PYSIDE_VERSION_STR, platform.system())
        QtGui.QMessageBox.about(self, title, msg)

    def select_files(self):
        """
        Select files to upload.

        """
        files = QtGui.QFileDialog.getOpenFileNames(
            self, self.tr("Select Files"),
            self._default_dir)
        for filename in files[0]:
            self._add_table_item(filename)

    def select_dir(self):
        dir_ = QtGui.QFileDialog.getExistingDirectory(
            self, self.tr("Select Directory"),
            self._default_dir)
        if dir_:
            self._default_dir = os.path.join(dir_, '..')
            self._add_table_item(dir_, 'd')

    def load_config(self):
        """
        Load config from file.
        """
        if not os.path.isfile(CONFIG_FILE):
            file_ = QtGui.QFileDialog.getOpenFileName(
                self, self.tr("Select Config File"),
                filter=self.tr("ConfigFiles(*.ini)"))
            if not file_:
                return
            file_ = file_[0]
        else:
            file_ = CONFIG_FILE
        self._load_default_config(file_)
        self.alert(self.tr("Config successfully loaded."))

    def _load_default_config(self, file_):
        config = dict4ini.DictIni(file_, hideData=True)
        for widget in self.required_fields:
            field_name = widget.objectName().replace("edt_", "")
            field_name = field_name.encode('utf-8')
            widget.setText(config.baniu.get(field_name, ""))
        dir_ = config.baniu.get("dir", '')
        if dir_:
            self._default_dir = dir_

    def save_config(self):
        """
        Save config to file.
        """
        self._save_config()
        self.alert(self.tr("Config was saved into {0}").format(CONFIG_FILE))

    def _save_config(self):
        if not os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "w") as f:
                f.write("")
        config = dict4ini.DictIni(CONFIG_FILE, hideData=True)
        for widget in self.required_fields:
            if widget.text():
                field_name = widget.objectName().replace("edt_", "")
                field_name = field_name.encode('utf-8')
                config.baniu[field_name] = widget.text()
        config.baniu['dir'] = self._default_dir
        config.save()

    def upload(self):
        """
        Walk the directories and get files recursively from it and upload
        the files to Qiniu CDN.

        """
        self.ui.statusbar.showMessage("Uploading...")
        checked = self._check_required()
        if not checked:
            return
        apikey, apisecret, self.bucket_name = self._get_required()
        self.prefix = self.ui.edt_prefix.text().encode('utf-8')
        items = self._get_table_items()
        if not items:
            self.alert(self.tr("Please select files or directories first."))
            return
        files = [item[0] for item in items if item[-1] == 'f']
        dirs = [item[0] for item in items if item[-1] == 'd']

        # First get files_with_dir: {"path": ['files_in_path', ...]}
        files_with_dir = {}
        for dir_ in dirs:
            files_with_dir[dir_] = self._get_all_files(dir_)
        files_with_dir.update({".": files})

        # Get filekeys for files and files in dirs.
        filekey_data = self._get_filekey_for_files(files_with_dir)

        # Threading upload.
        for item in filekey_data.iteritems():
            files_queue.put(item)

        self.ui.progressBar.setMaximum(files_queue.qsize())

        for i in range(10):
            self.task = ThreadingUploader(self.bucket_name, apikey, apisecret,
                                          self.prefix, parent=self)
            self.task.uploaded.connect(self.update_progress)
            self._thread_pool.append(self.task)

        for task in self._thread_pool:
            task.start()

    def update_progress(self, filekey):
        msg = self.tr("{0} upload success!").format(filekey)
        self.ui.statusbar.showMessage(msg)
        self.ui.progressBar.setValue(uploaded_queue.qsize())

    def closeEvent(self, event):
        self.exit()

    def exit(self):
        self._save_config()
        exit()

    def _get_filekey_for_files(self, files_with_dir):
        """
        Get filekey (used by qiniu upload) from files_with_dir.

        files_with_dir: {"root_path": ["abs_path_of_upload_file", ..]}

        Return:
            {"filekey": "abs_file_path"}

        """
        ret = {}
        for root_path, abs_path_list in files_with_dir.iteritems():
            temp = {}
            if root_path == '.':
                for file_ in abs_path_list:
                    root, tail = os.path.split(file_)
                    tail = tail.encode('utf-8')
                    temp[tail] = file_
            else:
                for file_ in abs_path_list:
                    # XXX: simply replace the root_path to ''

                    # Changed since 0.1.7:
                    # if root_path is not '.', we will upload files
                    # with root_path.last_leaf_name.
                    # Say if path `c:/os/os/aaa` was selected through
                    # self.select_dir, since 0.1.7, the upload filekey will
                    # be `aaa/bbb.css`
                    path_ = os.path.dirname(root_path)
                    filekey = file_.replace(
                        u"{}{}".format(path_, os.sep), '')
                    filekey = filekey.encode('utf-8')
                    filekey = filekey.replace(os.sep, '/')
                    temp[filekey] = file_
            ret.update(temp)
        return ret

    def _get_table_items(self, table_widget=None):
        """
        Get table data.

        Return:
          list table data.
        """
        if not table_widget:
            table_widget = self.ui.table_widget
        ret = []
        rows = table_widget.rowCount()
        columns = table_widget.columnCount()
        for row in range(rows):
            ret.append(
                [table_widget.item(row, column).text()
                    for column in range(columns)])
        return ret

    def _get_all_files(self, root_dir):
        """
        Recursively get the absolute path of the files in `root_dir`.

        Return:
         file_with_abs_path list.
        """
        ret_files = []
        for root, dirs, files in os.walk(root_dir):
            for file_ in files:
                file_name = os.path.join(root, file_)
                ret_files.append(file_name)
        return ret_files

    def _add_table_item(self, content, type_='f'):
        """
        Add (file_or_dir, type) to tableWidget.

        """
        item = QtGui.QTableWidgetItem(content)
        type_item = QtGui.QTableWidgetItem(type_)
        row_count = self.ui.table_widget.rowCount()
        self.ui.table_widget.setRowCount(row_count + 1)
        self.ui.table_widget.setItem(row_count, 0, item)
        self.ui.table_widget.setItem(row_count, 1, type_item)

    def _get_required(self):
        """
        Get text from the required lineEdit.
        """
        apikey = self.ui.edt_apikey.text()
        apikey = apikey.encode('utf-8')
        apisecret = self.ui.edt_apisecret.text()
        apisecret = apisecret.encode('utf-8')
        bucket_name = self.ui.edt_bucket_name.text()
        bucket_name = bucket_name.encode('utf-8')
        return apikey, apisecret, bucket_name

    def _check_required(self):
        """
        Check if the required field is blank or not.
        """
        for line_edit in self.required_fields:
            if not line_edit.text():
                name = line_edit.objectName().replace("edt_", "")
                msg = self.tr("%s must not be none.") % (name)
                self.alert(msg)
                line_edit.setFocus()
                return False
        return True

    def alert(self, msg):
        QtGui.QMessageBox.warning(self, self.tr("Notice"), msg)


class ThreadingUploader(QtCore.QThread):
    uploaded = QtCore.Signal(str)

    def __init__(self, bucket_name, apikey, apisecret, prefix='', parent=None):
        QtCore.QThread.__init__(self, parent)
        self.bucket = Bucket(bucket_name, apikey, apisecret)
        self.prefix = prefix

    def run(self):
        while files_queue.qsize() > 0:
            origin_filekey, realpath = files_queue.get()
            # FIXME: big file may act very slow.
            filelike = open(realpath, 'rb')
            if self.prefix:
                filekey = "{}{}".format(self.prefix, origin_filekey)
            else:
                filekey = origin_filekey
            resp = self.bucket.save(filekey, filelike)
            resp_key = resp['key'].encode('utf-8')
            assert resp_key == filekey, "{} != {}".format(resp_key, filekey)
            uploaded_queue.put(origin_filekey)
            self.uploaded.emit(resp['key'])


if __name__ == "__main__":
    app = QtGui.QApplication(sys.argv)
    locale = QtCore.QLocale.system().name()
    qt_translator = QtCore.QTranslator()
    if qt_translator.load("qt" + locale, ":/"):
        app.installTranslator(qt_translator)
    app_translator = QtCore.QTranslator()
    if app_translator.load("baniugui_" + locale, ":/resource/"):
        app.installTranslator(app_translator)
    app.setOrganizationName("X.Y")
    app.setOrganizationDomain("X.Y")
    app.setApplicationName(QtGui.QApplication.translate("Main", "Baniu Gui"))
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
