import torch.optim as optim
from sklearn import svm
from sklearn.metrics import accuracy_score
from torch.autograd.variable import Variable
from torch.optim import lr_scheduler
from loss import *
from network import *
from dataset import *
from sklearn.neighbors import KNeighborsClassifier
from torch.utils.data import DataLoader as DataLoader

g_hidden_channal = 64
d_hidden_channal = 64
image_channal = 1

generate = GeneratorNet()
generate.cuda()
# generate.weight_init(mean=0.0, std=0.02)
Tensor = torch.cuda.FloatTensor


def extract(v):
    return v.data.storage().tolist()


def stats(d):
    return [np.mean(d), np.std(d)]


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def get_generator_input_sampler():
    return lambda m, n: torch.rand(m, n)


def noise(size):
    # z = torch.randn(size, 784)
    # n = Variable(Tensor(np.random.normal(0, 1, (size, 100))))
    # z = Variable(2 * ((torch.randn(size, 100).bernoulli_(0.5)) - 0.5))
    z = Variable(Tensor(np.random.normal(0, 1, (size, 100))))
    return z.cuda()


# use real samples' pair/triplet to get pre-train model
def pre_train_epoch(pre_train_loader, model, criterion, optimizer, cuda, log_interval, metrics):
    # switch to train mode
    model.train()
    # print("length of data loader: ")
    losses = []
    total_loss = 0

    for batch_idx, (data1, data2, data3) in enumerate(pre_train_loader):
        data1, data2, data3 = data1.cuda(), data2.cuda(), data3.cuda()
        optimizer.zero_grad()
        embedded_x, embedded_y, embedded_z = model(data1, data2, data3)

        loss_outputs = criterion(embedded_x, embedded_y, embedded_z)
        loss = loss_outputs[0] if type(loss_outputs) in (tuple, list) else loss_outputs
        losses.append(loss.item())
        total_loss += loss.item()
        loss.backward()
        optimizer.step()

        for metric in metrics:
            metric(loss_outputs)

        if batch_idx % log_interval == 0:
            message = 'Train: [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                batch_idx * len(data1[0]), len(pre_train_loader.dataset),
                100. * batch_idx / len(pre_train_loader), np.mean(losses))
            for metric in metrics:
                message += '\t{}: {}'.format(metric.value())

            print(message)
            losses = []

    total_loss /= (batch_idx + 1)
    return total_loss, metrics


def train_metric(data1, data2, data3, fake_data, metric_criterion, metric_optimizer):
    metric_optimizer.zero_grad()
    loss = 0.3 * (metric_criterion(data1, data2, data3) + metric_criterion(data1, data2, fake_data))
    loss.backward()
    metric_optimizer.step()
    return loss


def train_generator(data1, data2, data3, fake_data, generator_criterion, generator_optimizer):
    generator_optimizer.zero_grad()
    loss = generator_criterion(data1, fake_data, data2, data3)
    loss.backward()
    generator_optimizer.step()
    return loss


# def train_generator(data1_ori, data1_d, data2_ori, data2_d, data3_ori, data3_d, fake_ori, fake_d,
#                     generator_criterion, generator_optimizer):
#     generator_optimizer.zero_grad()
#     loss = 1 * generator_criterion(data1_ori, data1_d, data2_ori, data2_d, data3_ori, data3_d, fake_ori, fake_d)
#     loss.backward()
#     generator_optimizer.step()
#     return loss


def train(train_loader, model, criterion_metric, criterion_gen, optimizer_metric, optimizer_gen, epoch):
    losses_metric = AverageMeter()
    losses_gen = AverageMeter()

    # switch to train mode
    model.train()
    for batch_idx, (data1, data2, data3) in enumerate(train_loader):
        data1, data2, data3 = data1.cuda(), data2.cuda(), data3.cuda()
        N = data1.size(0)
        # print("N is: ", N)
        # compute output
        embedded_x, embedded_y, embedded_z = model(data1, data2, data3)
        noise_data = noise(N)
        fake_data = generate(noise_data)
        e_fake_data1, e_fake_data2, e_fake_data3 = model(fake_data, fake_data, fake_data)

        # train metric on real triplet and fake triplet
        metric_loss1 = train_metric(embedded_x, embedded_y, embedded_z, e_fake_data1.detach(),
                                    criterion_metric, optimizer_metric)
        # metric_loss2 = train_metric(embedded_x, embedded_y, e_fake_data1.data, criterion_metric, optimizer_metric)

        metric_loss = metric_loss1

        # train generator
        generator_loss = train_generator(embedded_x.detach(), embedded_y.detach(), embedded_z.detach(),
                                         e_fake_data1, criterion_gen, optimizer_gen)
        # generator_loss = train_generator(data1.detach(), embedded_x.detach(), data2.detach(), embedded_y.detach(), data3.detach(),
        #                                         embedded_z.detach(), fake_data, e_fake_data1, criterion_gen, optimizer_gen)

        # loss = generator_loss + 0.1 * metric_loss
        losses_metric.update(metric_loss.data[0], data1.size(0))
        losses_gen.update(generator_loss.data[0], data1.size(0))

        if batch_idx % 5 == 0:
            print('Train Epoch: {} [{}/{}]\t'
                  'metric & gen Loss: {:.4f} & {:.4f}\t'.format(
                epoch, batch_idx * len(data1), len(train_loader.dataset), losses_metric.avg, losses_gen.avg))
            # print("%s: Metric: %s Generator: " % (epoch, extract(metric_loss)[0], extract(generator_loss)[0]))


def svm_test(X, Y, split):
    svc = svm.SVC(kernel='linear', C=32, gamma=0.1)
    train_x = X[0:split]
    train_y = Y[0:split]

    test_x = X[split:]
    test_y = Y[split:]

    svc.fit(train_x, train_y)
    predictions = svc.predict(test_x)
    accuracy = accuracy_score(test_y, predictions)
    # neigh = KNeighborsClassifier(n_neighbors=10)
    # neigh.fit(train_x, train_y)
    # predictions = neigh.predict(test_x)
    # accuracy = accuracy_score(test_y, predictions)
    return accuracy


if __name__ == "__main__":
    dataset_path = '/home/wzy/Coding/Data/metric_learning/fashion-mnist.csv'
    dataset, classes = read_dataset(dataset_path)
    class_count = len(classes)
    split = 8000
    pre_train_split = split/2
    pre_train_data = dataset[0:7800]
    train_data = dataset[2000:8000]
    test_data = dataset
    margin = 0.5
    lambda1 = 0.001
    lambda2 = 60
    pre_epochs = 50
    # often setting to more than 10000
    train_epochs = 10000

    pre_train_dataset = TripletDataSet(pre_train_data)
    train_dataset = TripletDataSet(train_data)
    test_dataset = Test_Dataset(test_data)

    # metric learning model initial
    net = EmbeddingNet()
    # net.weight_init(mean=0.0, std=0.02)
    # net.apply(initialize_weights)
    model = TripletNet(net)
    model.cuda()

    criterion_triplet = TripletLoss(margin)
    # criterion = generateLoss(margin, lambda1, lambda2)
    criterion_g = generateLoss(margin, lambda1, lambda2)
    # optimizer_triplet = optim.SGD(model.parameters(), lr=0.01, momentum=0.9)
    # optimizer_triplet = optim.SGD(model.parameters(), lr=0.005, momentum=0.9)
    optimizer_triplet = optim.Adam(model.parameters(), lr=0.001, betas=(0.5, 0.999))
    scheduler = lr_scheduler.StepLR(optimizer_triplet, 10, gamma=0.1, last_epoch=-1)
    optimizer_g = optim.Adam(generate.parameters(), lr=0.0002, betas=(0.5, 0.999))
    scheduler_g = lr_scheduler.StepLR(optimizer_g, 100, gamma=0.5, last_epoch=-1)
    pre_dataloader = DataLoader(dataset=pre_train_dataset, shuffle=True, batch_size=128)
    train_dataloader = DataLoader(dataset=train_dataset, shuffle=True, batch_size=128)
    test_dataloader = DataLoader(dataset=test_dataset, shuffle=False, batch_size=1)

    # first, do pre-train
    print("start pre-train")
    start_epoch = 0
    metrics = []
    log_interval = 200
    # train for one epoch
    for epoch in range(0, start_epoch):
        scheduler.step()
    for epoch in range(start_epoch, pre_epochs):
        scheduler.step()
        # pre_train(pre_dataloader, model, criterion_triplet, optimizer_triplet, epoch)
        train_loss, metrics = pre_train_epoch(pre_dataloader, model, criterion_triplet,
                                              optimizer_triplet, cuda, log_interval, metrics)
        message = 'Epoch: {}/{}. Train set: Average loss: {:.4f}'.format(epoch + 1, pre_epochs, train_loss)
        for metric in metrics:
            message += '\t{}: {}'.format(metric.name(), metric.value())

        print(message)

    # start joint train g and metric
    print("start train metric and adversarial")
    for epoch in range(0, start_epoch):
        scheduler_g.step()
    for epoch in range(start_epoch, train_epochs):
        scheduler_g.step()
        train(train_dataloader, model, criterion_triplet, criterion_g, optimizer_triplet, optimizer_g, epoch)

    # start test
    print("start test")
    X = []
    Y = []
    for s in test_dataloader:
        # x = s[0]
        x = s[0].cuda()
        x1, x2, x3 = model(x, x, x)
        X.append(x1.data.cpu().squeeze().numpy())
        # print(int(s[1][0]))
        y = int(s[1][0])
        Y.append(y)

    X = np.array(X)
    print(len(X))
    Y = np.array(Y)
    svm_accuracy = svm_test(X, Y, split)
    print("acc is", svm_accuracy)
